#!/usr/bin/env python3
"""Per-outfall model of Conham E. coli, using individual CSO sites (not distance bands).

This is a sibling to ``scripts/model_conham_ecoli.py`` (which groups upstream CSO
spills into distance bands). Here, each individual storm-overflow outfall on the
Conham watercourses is its own feature, so we can ask **which specific outfalls**
are associated with worse E. coli at Conham rather than just "spills within N
miles".

Data source
-----------
The Wessex Water *Event Duration Monitoring 2025* ArcGIS view -- the same source
``scripts/analyze_conham_cso_ecoli.py`` uses (NOT the live storm-overflow feed in
``poo.py``):

    https://services.arcgis.com/3SZ6e0uCvPROr4mS/arcgis/rest/services/Wessex_Water_Event_Duration_Monitoring_2025_view/FeatureServer/0/query

Because querying ArcGIS needs network egress, the script is split into two steps:

    python scripts/model_conham_ecoli_by_site.py fetch   # -> per-site features CSV
    python scripts/model_conham_ecoli_by_site.py model   # -> ranking + predictions

``all`` runs both. The ``model`` step is pure offline computation on the cached
CSV, so it can be re-run and tuned without touching the network.

Method
------
For each E. coli sample date a 7-day event window is fetched once, and per-outfall
spill hours are accumulated for every 1- to 7-day lookback. The ``model`` step
then:

1. ranks each outfall by the univariate correlation of log1p(spill hours) with
   log10(E. coli) -- this is the "which ones matter most" answer;
2. builds a parsimonious multi-outfall model by forward selection scored with
   leave-one-out cross-validation (LOOCV), since there are far more outfalls than
   samples;
3. reports the per-day percentage error of that model.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path

ARCGIS_QUERY_URL = "https://services.arcgis.com/3SZ6e0uCvPROr4mS/arcgis/rest/services/Wessex_Water_Event_Duration_Monitoring_2025_view/FeatureServer/0/query"
CONHAM_RIVERS = [
    "RIVER AVON",
    "RIVER CHEW",
    "charlton bottom via sws",
    "bathford brook (s)",
    "horsecombe brook",
    "river avon via sws",
    "river avon (via sws)",
]
CONHAM_LAT = 51.444858
CONHAM_LON = -2.534812
MAX_LOOKBACK = 7

SITE_FEATURES_CSV = "docs/data/conham_cso_site_features.csv"
PREDICTIONS_CSV = "docs/data/conham_ecoli_site_model_predictions.csv"
REPORT_MD = "docs/data/conham_ecoli_site_model.md"

# Modelling knobs.
DEFAULT_LOOKBACK = 7          # best window for the band model; reused here
DEFAULT_RIDGE = 0.3           # standardised-feature ridge penalty
MAX_SELECTED_OUTFALLS = 4     # cap on forward-selected outfalls (n=25 samples)
MIN_ACTIVE_WINDOWS = 3        # an outfall must spill in >= this many windows to be a candidate


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def ms_to_datetime(value):
    if value in (None, 0):
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def read_samples(path: Path) -> list[dict[str, object]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {
            "sample_date": date.fromisoformat(row["sample_date"]),
            "e_coli_cfu_per_100ml": float(row["cfu_per_100ml"]),
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Step 1: fetch per-outfall spill features from ArcGIS
# --------------------------------------------------------------------------- #
def arcgis_where(start: datetime, end: datetime) -> str:
    river_clause = " OR ".join(f"ReceivingWatercourse = '{river}'" for river in CONHAM_RIVERS)
    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_s = end.strftime("%Y-%m-%d %H:%M:%S")
    return f"({river_clause}) AND EventStart >= DATE '{start_s}' AND EventStart < DATE '{end_s}'"


def fetch_window_events(start: datetime, end: datetime, page_size: int, sleep_seconds: float) -> list[dict]:
    features: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": arcgis_where(start, end),
            "outFields": "SiteId,SiteName,ReceivingWatercourse,EventId,EventStart,EventEnd,Duration,OutfallLatitude,OutfallLongitude",
            "orderByFields": "EventStart ASC",
            "f": "json",
            "resultRecordCount": str(page_size),
            "resultOffset": str(offset),
            "returnExceededLimitFeatures": "true",
        }
        url = ARCGIS_QUERY_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)
        if "error" in data:
            raise RuntimeError(json.dumps(data["error"], indent=2))
        page = data.get("features", [])
        if not isinstance(page, list):
            raise RuntimeError("ArcGIS response did not contain a feature list")
        features.extend(page)
        if len(page) < page_size or not data.get("exceededTransferLimit"):
            break
        offset += page_size
        time.sleep(sleep_seconds)
    return features


def event_duration_hours(attrs: dict, window_end: datetime) -> float | None:
    start = ms_to_datetime(attrs.get("EventStart"))
    if start is None:
        return None
    end = ms_to_datetime(attrs.get("EventEnd")) or window_end
    hours = (end - start).total_seconds() / 3600
    return hours if hours > 0 else 0.0


def fetch_site_features(samples: list[dict], page_size: int, sleep_seconds: float) -> list[dict]:
    """One ArcGIS query per sample date (7-day window); derive every lookback by filtering."""
    rows: list[dict] = []
    for sample in samples:
        sample_end = datetime.combine(sample["sample_date"], dt_time.min, tzinfo=timezone.utc)
        window_start = sample_end - timedelta(days=MAX_LOOKBACK)
        events = fetch_window_events(window_start, sample_end, page_size, sleep_seconds)
        for lookback in range(1, MAX_LOOKBACK + 1):
            lookback_start = sample_end - timedelta(days=lookback)
            per_site: dict[tuple, dict] = {}
            for feature in events:
                attrs = feature.get("attributes", {})
                start = ms_to_datetime(attrs.get("EventStart"))
                if start is None or start < lookback_start:
                    continue
                lat, lon = attrs.get("OutfallLatitude"), attrs.get("OutfallLongitude")
                hours = event_duration_hours(attrs, sample_end)
                if hours is None:
                    continue
                site_id = attrs.get("SiteId")
                site_name = attrs.get("SiteName")
                key = (site_id, site_name)
                bucket = per_site.setdefault(
                    key,
                    {
                        "site_id": site_id,
                        "site_name": site_name,
                        "receiving_watercourse": attrs.get("ReceivingWatercourse"),
                        "outfall_lat": lat,
                        "outfall_lon": lon,
                        "spill_hours": 0.0,
                        "event_count": 0,
                    },
                )
                bucket["spill_hours"] += hours
                bucket["event_count"] += 1
            for bucket in per_site.values():
                lat, lon = bucket["outfall_lat"], bucket["outfall_lon"]
                distance = haversine(CONHAM_LAT, CONHAM_LON, float(lat), float(lon)) if lat and lon else ""
                rows.append(
                    {
                        "sample_date": sample["sample_date"].isoformat(),
                        "e_coli_cfu_per_100ml": sample["e_coli_cfu_per_100ml"],
                        "lookback_days": lookback,
                        "site_id": bucket["site_id"],
                        "site_name": bucket["site_name"],
                        "receiving_watercourse": bucket["receiving_watercourse"],
                        "outfall_lat": lat,
                        "outfall_lon": lon,
                        "distance_miles": round(distance, 3) if distance != "" else "",
                        "spill_hours": round(bucket["spill_hours"], 3),
                        "event_count": bucket["event_count"],
                    }
                )
        time.sleep(sleep_seconds)
    return rows


def write_site_features(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sample_date", "e_coli_cfu_per_100ml", "lookback_days", "site_id", "site_name",
        "receiving_watercourse", "outfall_lat", "outfall_lon", "distance_miles",
        "spill_hours", "event_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- #
# Linear algebra (standard library only)
# --------------------------------------------------------------------------- #
def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return float("nan")
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def standardise_fit(matrix):
    if not matrix or not matrix[0]:
        return []
    stats = []
    for j in range(len(matrix[0])):
        col = [row[j] for row in matrix]
        mean = sum(col) / len(col)
        std = math.sqrt(sum((v - mean) ** 2 for v in col) / len(col))
        stats.append((mean, std))
    return stats


def standardise_apply(matrix, stats):
    return [
        [(row[j] - stats[j][0]) / stats[j][1] if stats[j][1] > 0 else 0.0 for j in range(len(stats))]
        for row in matrix
    ]


def solve_ridge(matrix, target, ridge):
    n = len(matrix)
    p = len(matrix[0]) if matrix and matrix[0] else 0
    design = [[1.0] + row for row in matrix]
    width = p + 1
    xtx = [[sum(design[k][i] * design[k][j] for k in range(n)) for j in range(width)] for i in range(width)]
    xty = [sum(design[k][i] * target[k] for k in range(n)) for i in range(width)]
    for i in range(1, width):
        xtx[i][i] += ridge
    aug = [xtx[i] + [xty[i]] for i in range(width)]
    for col in range(width):
        pivot = max(range(col, width), key=lambda r: abs(aug[r][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_val = aug[col][col]
        if abs(pivot_val) < 1e-12:
            continue
        for r in range(width):
            if r == col:
                continue
            factor = aug[r][col] / pivot_val
            aug[r] = [aug[r][k] - factor * aug[col][k] for k in range(width + 1)]
    return [aug[i][width] / aug[i][i] if abs(aug[i][i]) > 1e-12 else 0.0 for i in range(width)]


# --------------------------------------------------------------------------- #
# Step 2: model
# --------------------------------------------------------------------------- #
def load_site_features(path: Path, samples_path: Path, lookback: int):
    """Return (dates, ecoli, site_meta, spill[date][site]) at a fixed lookback.

    The authoritative date/E. coli list comes from the sampling CSV so that
    sample dates with no spill anywhere (no rows in the per-outfall CSV) are kept
    as all-zero feature rows rather than silently dropped.
    """
    ecoli: dict[str, float] = {row["sample_date"]: row["e_coli_cfu_per_100ml"] for row in (
        {"sample_date": s["sample_date"].isoformat(), "e_coli_cfu_per_100ml": s["e_coli_cfu_per_100ml"]}
        for s in read_samples(samples_path)
    )}
    spill: dict[str, dict[str, float]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if int(row["lookback_days"]) != lookback:
                continue
            d = row["sample_date"]
            if d not in ecoli:  # ignore stray dates not in the sampling CSV
                continue
            site = row["site_name"] or row["site_id"]
            spill[d][site] = spill[d].get(site, 0.0) + float(row["spill_hours"])
            meta.setdefault(site, {"distance_miles": row.get("distance_miles", ""), "watercourse": row.get("receiving_watercourse", "")})
    dates = sorted(ecoli)
    return dates, ecoli, meta, spill


def site_vector(dates, spill, site) -> list[float]:
    return [math.log1p(spill[d].get(site, 0.0)) for d in dates]


def loocv_mae_log(dates, ecoli, spill, sites, ridge) -> tuple[float, dict[str, float]]:
    """LOOCV mean absolute log10 error for a fixed set of outfall features."""
    preds: dict[str, float] = {}
    abs_log = []
    for test in dates:
        train = [d for d in dates if d != test]
        matrix = [[math.log1p(spill[d].get(s, 0.0)) for s in sites] for d in train]
        stats = standardise_fit(matrix) if sites else []
        std_train = standardise_apply(matrix, stats) if sites else [[] for _ in train]
        target = [math.log10(ecoli[d]) for d in train]
        beta = solve_ridge(std_train, target, ridge)
        test_row = standardise_apply([[math.log1p(spill[test].get(s, 0.0)) for s in sites]], stats)[0] if sites else []
        log_pred = beta[0] + sum(beta[1 + j] * test_row[j] for j in range(len(sites)))
        preds[test] = log_pred
        abs_log.append(abs(log_pred - math.log10(ecoli[test])))
    return sum(abs_log) / len(abs_log), preds


def forward_select(dates, ecoli, spill, candidates, ridge, max_features):
    """Greedily add the outfall that most improves LOOCV MAE_log; stop when no gain."""
    selected: list[str] = []
    best_mae, _ = loocv_mae_log(dates, ecoli, spill, selected, ridge)
    history = [("(intercept only)", best_mae)]
    while len(selected) < max_features:
        trials = []
        for site in candidates:
            if site in selected:
                continue
            mae, _ = loocv_mae_log(dates, ecoli, spill, selected + [site], ridge)
            trials.append((mae, site))
        if not trials:
            break
        trials.sort()
        mae, site = trials[0]
        if mae >= best_mae - 1e-4:  # require a real improvement
            break
        selected.append(site)
        best_mae = mae
        history.append((site, mae))
    return selected, best_mae, history


def rank_outfalls(dates, ecoli, spill, meta):
    y = [math.log10(ecoli[d]) for d in dates]
    rows = []
    for site in sorted({s for d in dates for s in spill[d]}):
        values = [spill[d].get(site, 0.0) for d in dates]
        active = sum(1 for v in values if v > 0)
        if active == 0:
            continue
        r = pearson([math.log1p(v) for v in values], y)
        rows.append(
            {
                "site": site,
                "watercourse": meta.get(site, {}).get("watercourse", ""),
                "distance_miles": meta.get(site, {}).get("distance_miles", ""),
                "active_windows": active,
                "total_spill_hours": round(sum(values), 1),
                "pearson_r": r,
            }
        )
    rows.sort(key=lambda r: (-(abs(r["pearson_r"]) if not math.isnan(r["pearson_r"]) else -1)))
    return rows


def run_model(args) -> int:
    path = Path(args.features)
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Run `python {Path(__file__).name} fetch` first "
            "(needs network access to the ArcGIS 2025 EDM view)."
        )
    dates, ecoli, meta, spill = load_site_features(path, Path(args.samples), args.lookback)
    if not dates:
        raise SystemExit(f"No rows at lookback_days={args.lookback} in {path}")

    ranking = rank_outfalls(dates, ecoli, spill, meta)
    candidates = [r["site"] for r in ranking if r["active_windows"] >= MIN_ACTIVE_WINDOWS]
    selected, sel_mae, history = forward_select(dates, ecoli, spill, candidates, args.ridge, args.max_outfalls)

    # Final model: full-fit coefficients (impact direction) + LOOCV per-day errors.
    matrix = [[math.log1p(spill[d].get(s, 0.0)) for s in selected] for d in dates]
    stats = standardise_fit(matrix) if selected else []
    std = standardise_apply(matrix, stats) if selected else [[] for _ in dates]
    beta = solve_ridge(std, [math.log10(ecoli[d]) for d in dates], args.ridge)
    _, loocv_log = loocv_mae_log(dates, ecoli, spill, selected, args.ridge)
    mean_mae, _ = loocv_mae_log(dates, ecoli, spill, [], args.ridge)

    predictions, apes = [], []
    for d in dates:
        actual = ecoli[d]
        pred = 10 ** loocv_log[d]
        ape = abs(pred - actual) / actual * 100.0
        apes.append(ape)
        predictions.append(
            {
                "sample_date": d,
                "actual_cfu_per_100ml": round(actual, 1),
                "loocv_predicted_cfu_per_100ml": round(pred, 1),
                "loocv_signed_pct_error": round((pred - actual) / actual * 100.0, 1),
                "loocv_abs_pct_error": round(ape, 1),
            }
        )
    median_ape = sorted(apes)[len(apes) // 2]
    mape = sum(apes) / len(apes)

    out_csv = Path(args.predictions)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_date", "actual_cfu_per_100ml", "loocv_predicted_cfu_per_100ml", "loocv_signed_pct_error", "loocv_abs_pct_error"],
        )
        writer.writeheader()
        writer.writerows(predictions)

    write_report(Path(args.report), dates, args.lookback, ranking, selected, beta, stats, history,
                 sel_mae, mean_mae, median_ape, mape, predictions)
    print(f"Wrote {out_csv}")
    print(f"Wrote {args.report}")
    print(f"Outfalls considered: {len(ranking)}; selected: {len(selected)}")
    print(f"LOOCV MAE_log: selected {sel_mae:.3f} vs mean baseline {mean_mae:.3f}; median APE {median_ape:.1f}%")
    return 0


def write_report(path, dates, lookback, ranking, selected, beta, stats, history,
                 sel_mae, mean_mae, median_ape, mape, predictions) -> None:
    lines = [
        "# Conham E. coli model by individual CSO outfall",
        "",
        "Generated by `scripts/model_conham_ecoli_by_site.py`. Unlike",
        "`conham_ecoli_model.md` (distance bands), each upstream storm-overflow outfall",
        "is its own feature, so we can see **which specific outfalls** track E. coli at",
        f"Conham. Features are log1p(spill hours) per outfall over a {lookback}-day lookback,",
        "from the Wessex Water Event Duration Monitoring 2025 ArcGIS view.",
        "",
        f"- Sample dates: {len(dates)}",
        f"- Outfalls active in the windows: {len(ranking)}",
        "",
        "## Which outfalls track E. coli most?",
        "",
        "Univariate correlation of each outfall's log1p(spill hours) with log10(E. coli).",
        "Positive `r` = more spilling at that outfall coincides with higher E. coli.",
        "`active windows` is how many of the sample windows that outfall actually spilled in",
        "(low counts mean a fragile correlation).",
        "",
        "| Rank | Outfall | Watercourse | Dist (mi) | Active windows | Total spill h | Pearson r |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(ranking[:20], 1):
        rv = "n/a" if math.isnan(r["pearson_r"]) else f"{r['pearson_r']:+.3f}"
        sel = " ★" if r["site"] in selected else ""
        lines.append(
            f"| {i} | {r['site']}{sel} | {r['watercourse']} | {r['distance_miles']} | "
            f"{r['active_windows']} | {r['total_spill_hours']:.0f} | {rv} |"
        )
    lines.extend(
        [
            "",
            "★ = chosen by the cross-validated multi-outfall model below.",
            "",
            "## Cross-validated multi-outfall model",
            "",
            "Outfalls were added greedily, each step keeping the one that most improved",
            "leave-one-out cross-validation (LOOCV). Selection stops when no outfall helps.",
            "",
            "| Step | Added outfall | LOOCV MAE_log |",
            "|---:|---|---:|",
        ]
    )
    for i, (site, mae) in enumerate(history):
        lines.append(f"| {i} | {site} | {mae:.3f} |")
    lines.extend(
        [
            "",
            f"Selected model LOOCV MAE_log **{sel_mae:.3f}** vs mean-only baseline {mean_mae:.3f}.",
            "(A distance-band model on the same data scores ~0.44; see `conham_ecoli_model.md`.)",
            "",
            "### Coefficients (standardised log1p spill hours, log10 target)",
            "",
            "| Term | Coefficient |",
            "|---|---:|",
            f"| intercept | {beta[0]:.4f} |",
        ]
    )
    for site, coef in zip(selected, beta[1:]):
        lines.append(f"| {site} | {coef:+.4f} |")
    if not selected:
        lines.append("| _(no outfall improved LOOCV; model is intercept-only)_ | |")
    lines.extend(
        [
            "",
            "## Per-day percentage error (leave-one-out)",
            "",
            f"Median absolute error **{median_ape:.1f}%**, MAPE {mape:.1f}%.",
            "",
            "| Sample date | Actual | LOOCV predicted | Signed % error | Abs % error |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for p in predictions:
        lines.append(
            f"| {p['sample_date']} | {p['actual_cfu_per_100ml']:.0f} | {p['loocv_predicted_cfu_per_100ml']:.0f} | "
            f"{p['loocv_signed_pct_error']:+.1f}% | {p['loocv_abs_pct_error']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Only 25 sample dates but many outfalls, so single-outfall correlations are",
            "  noisy and the multi-outfall selection can latch onto coincidences. Treat the",
            "  ranking as a shortlist of candidates, not proof of causation.",
            "- Nearby outfalls spill together in the same rain, so their spill series are",
            "  highly collinear and have near-identical correlations. The model cannot",
            "  cleanly separate one culprit; read the top of the ranking as a *cluster*",
            "  (here, Bath-area River Avon outfalls) rather than a single source. For the",
            "  same reason the multi-outfall fit can assign negative coefficients to",
            "  collinear partners (suppressor effects) -- those are statistical artefacts,",
            "  not evidence that an outfall improves water quality.",
            "- Forward selection used all dates to choose outfalls; the reported LOOCV error",
            "  for the chosen set is therefore mildly optimistic.",
            "- E. coli values are chart-digitised and right-censored at 1000 CFU/100ml.",
            "- An outfall can rank low simply because it rarely spilled during the sampling",
            "  window, not because its discharges are clean.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def run_fetch(args) -> int:
    samples = read_samples(Path(args.input))
    try:
        rows = fetch_site_features(samples, args.page_size, args.sleep)
    except urllib.error.URLError as exc:
        raise SystemExit(
            "Could not reach the ArcGIS 2025 EDM view "
            f"({ARCGIS_QUERY_URL.split('/services/')[0]}): {exc}.\n"
            "Run `fetch` from an environment with outbound access to services.arcgis.com, "
            "commit the resulting CSV, then run the `model` step."
        )
    write_site_features(rows, Path(args.features))
    print(f"Wrote {args.features} ({len(rows)} site-window rows from {len(samples)} sample dates)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    common_features = dict(default=SITE_FEATURES_CSV, help="Per-outfall features CSV")

    f = sub.add_parser("fetch", help="Query ArcGIS for per-outfall spill features (needs network)")
    f.add_argument("--input", default="docs/data/conham_sampling_2025_2026_e_coli.csv")
    f.add_argument("--features", **common_features)
    f.add_argument("--page-size", type=int, default=2000)
    f.add_argument("--sleep", type=float, default=0.1)
    f.set_defaults(func=run_fetch)

    m = sub.add_parser("model", help="Rank outfalls and fit the model from the cached CSV (offline)")
    m.add_argument("--features", **common_features)
    m.add_argument("--samples", default="docs/data/conham_sampling_2025_2026_e_coli.csv", help="E. coli sampling CSV (authoritative date list)")
    m.add_argument("--predictions", default=PREDICTIONS_CSV)
    m.add_argument("--report", default=REPORT_MD)
    m.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    m.add_argument("--ridge", type=float, default=DEFAULT_RIDGE)
    m.add_argument("--max-outfalls", type=int, default=MAX_SELECTED_OUTFALLS)
    m.set_defaults(func=run_model)

    a = sub.add_parser("all", help="fetch then model")
    a.add_argument("--input", default="docs/data/conham_sampling_2025_2026_e_coli.csv")
    a.add_argument("--samples", default="docs/data/conham_sampling_2025_2026_e_coli.csv", help="E. coli sampling CSV (authoritative date list)")
    a.add_argument("--features", **common_features)
    a.add_argument("--predictions", default=PREDICTIONS_CSV)
    a.add_argument("--report", default=REPORT_MD)
    a.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    a.add_argument("--ridge", type=float, default=DEFAULT_RIDGE)
    a.add_argument("--max-outfalls", type=int, default=MAX_SELECTED_OUTFALLS)
    a.add_argument("--page-size", type=int, default=2000)
    a.add_argument("--sleep", type=float, default=0.1)
    a.set_defaults(func=lambda args: run_fetch(args) or run_model(args))
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
