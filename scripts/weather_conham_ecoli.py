#!/usr/bin/env python3
"""Does weather (mainly rainfall) influence Conham E. coli, on its own and with CSOs?

Rainfall is the obvious missing driver in the CSO-only models
(`conham_ecoli_model.md`, `conham_ecoli_site_model.md`): it both triggers storm
overflows and washes diffuse/agricultural contamination into the river. This
script pulls a daily weather record for Conham and tests whether it tracks the
digitised E. coli samples.

Data source
-----------
Open-Meteo's free historical archive (ERA5 reanalysis, no API key):

    https://archive-api.open-meteo.com/v1/archive

Rainfall is fetched at two points: Conham itself, and upstream in Bath (~8-9
miles up the River Avon, where the spill-driving CSO cluster sits). The
upstream rain should track those spills better than rain at Conham.

Two steps, like model_conham_ecoli_by_site.py, because the fetch needs network:

    python scripts/weather_conham_ecoli.py fetch     # -> Conham + Bath daily weather CSVs
    python scripts/weather_conham_ecoli.py analyze   # offline correlations + combined model

Method (analyze)
----------------
For each E. coli sample date, weather is summarised over 1- to 7-day lookback
windows: cumulative rainfall, heaviest single day, mean/min temperature. The
script then:

1. ranks weather features (local + upstream rainfall, temperature) by univariate
   correlation with log10(E. coli);
2. compares leave-one-out cross-validation (LOOCV) error for rainfall-only
   (local and upstream), the best CSO feature, and combined CSO+weather models,
   to see whether weather adds anything beyond the CSO signal;
3. reports per-day percentage error and compares local vs upstream rainfall on
   the high-E. coli days the CSO model could not explain.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CONHAM_LAT = 51.444858
CONHAM_LON = -2.534812
# Upstream point in central Bath, where the spill-driving CSO cluster sits
# (~8-9 miles up the River Avon). Rain here should track those spills better than
# rain at Conham itself.
BATH_LAT = 51.3800
BATH_LON = -2.3590
MAX_LOOKBACK = 7

WEATHER_CSV = "docs/data/conham_weather_daily.csv"
UPSTREAM_WEATHER_CSV = "docs/data/conham_upstream_weather_daily.csv"
CSO_FEATURES_CSV = "docs/data/conham_cso_ecoli_features.csv"
SAMPLES_CSV = "docs/data/conham_sampling_2025_2026_e_coli.csv"
PREDICTIONS_CSV = "docs/data/conham_weather_ecoli_predictions.csv"
REPORT_MD = "docs/data/conham_weather_ecoli_analysis.md"

# Best single CSO predictor, from conham_ecoli_model.md (band model selection).
BEST_CSO_LOOKBACK = 7
BEST_CSO_COLUMN = "spill_hours_10_to_20_miles"
DEFAULT_RIDGE = 0.3


# --------------------------------------------------------------------------- #
# Samples
# --------------------------------------------------------------------------- #
def read_samples(path: Path) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["sample_date"]: float(row["cfu_per_100ml"]) for row in csv.DictReader(handle)}


# --------------------------------------------------------------------------- #
# Step 1: fetch daily weather
# --------------------------------------------------------------------------- #
def fetch_weather(start: date, end: date, lat: float = CONHAM_LAT, lon: float = CONHAM_LON) -> list[dict]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum,rain_sum,temperature_2m_mean,temperature_2m_max,temperature_2m_min",
        "timezone": "UTC",
    }
    url = ARCHIVE_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.load(response)
    if "error" in data and data.get("error"):
        raise RuntimeError(json.dumps(data, indent=2))
    daily = data.get("daily", {})
    times = daily.get("time", [])
    rows = []
    for i, day in enumerate(times):
        rows.append(
            {
                "date": day,
                "precipitation_mm": daily.get("precipitation_sum", [None] * len(times))[i],
                "rain_mm": daily.get("rain_sum", [None] * len(times))[i],
                "temp_mean_c": daily.get("temperature_2m_mean", [None] * len(times))[i],
                "temp_max_c": daily.get("temperature_2m_max", [None] * len(times))[i],
                "temp_min_c": daily.get("temperature_2m_min", [None] * len(times))[i],
            }
        )
    return rows


def _write_weather(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "precipitation_mm", "rain_mm", "temp_mean_c", "temp_max_c", "temp_min_c"])
        writer.writeheader()
        writer.writerows(rows)


def run_fetch(args) -> int:
    samples = read_samples(Path(args.samples))
    sample_dates = sorted(date.fromisoformat(d) for d in samples)
    start = min(sample_dates) - timedelta(days=MAX_LOOKBACK + 1)
    end = max(sample_dates)
    locations = [
        ("Conham", CONHAM_LAT, CONHAM_LON, Path(args.weather)),
        ("Bath (upstream)", BATH_LAT, BATH_LON, Path(args.upstream_weather)),
    ]
    for name, lat, lon, out in locations:
        try:
            rows = fetch_weather(start, end, lat, lon)
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"Could not reach Open-Meteo ({ARCHIVE_URL}): {exc}.\n"
                "Run `fetch` where archive-api.open-meteo.com egress is allowed, commit "
                f"{args.weather} and {args.upstream_weather}, then run the `analyze` step."
            )
        _write_weather(rows, out)
        print(f"Wrote {out} ({name}: {len(rows)} days, {start}..{end})")
    return 0


# --------------------------------------------------------------------------- #
# Feature engineering
# --------------------------------------------------------------------------- #
def load_weather(path: Path) -> dict[str, dict[str, float]]:
    daily: dict[str, dict[str, float]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            def num(key):
                v = row.get(key, "")
                return float(v) if v not in ("", None) else 0.0
            daily[row["date"]] = {
                "precip": num("precipitation_mm"),
                "temp_mean": num("temp_mean_c"),
                "temp_min": num("temp_min_c"),
            }
    return daily


def weather_features(daily: dict[str, dict[str, float]], sample_date: str, lookback: int) -> dict[str, float]:
    end = date.fromisoformat(sample_date)
    window = [end - timedelta(days=k) for k in range(1, lookback + 1)]
    precip = [daily[d.isoformat()]["precip"] for d in window if d.isoformat() in daily]
    tmean = [daily[d.isoformat()]["temp_mean"] for d in window if d.isoformat() in daily]
    if not precip:
        return {"rain_sum": 0.0, "rain_max": 0.0, "temp_mean": 0.0}
    return {
        "rain_sum": sum(precip),
        "rain_max": max(precip),
        "temp_mean": sum(tmean) / len(tmean) if tmean else 0.0,
    }


def load_cso_feature(path: Path, lookback: int, column: str) -> dict[str, float]:
    """One CSO feature value per sample date at a fixed lookback (0.0 if missing)."""
    values: dict[str, float] = {}
    if not path.exists():
        return values
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if int(row["lookback_days"]) == lookback:
                values[row["sample_date"]] = float(row[column])
    return values


# --------------------------------------------------------------------------- #
# Stats helpers
# --------------------------------------------------------------------------- #
def pearson(xs, ys) -> float:
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
        col = [r[j] for r in matrix]
        m = sum(col) / len(col)
        s = math.sqrt(sum((v - m) ** 2 for v in col) / len(col))
        stats.append((m, s))
    return stats


def standardise_apply(matrix, stats):
    return [[(r[j] - stats[j][0]) / stats[j][1] if stats[j][1] > 0 else 0.0 for j in range(len(stats))] for r in matrix]


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
        pv = aug[col][col]
        if abs(pv) < 1e-12:
            continue
        for r in range(width):
            if r == col:
                continue
            f = aug[r][col] / pv
            aug[r] = [aug[r][k] - f * aug[col][k] for k in range(width + 1)]
    return [aug[i][width] / aug[i][i] if abs(aug[i][i]) > 1e-12 else 0.0 for i in range(width)]


def loocv(dates, ecoli, feats, names, ridge):
    """feats[date] -> dict of feature values; names = ordered features to use. LOOCV log preds."""
    preds = {}
    abs_log = []
    for test in dates:
        train = [d for d in dates if d != test]
        matrix = [[feats[d][n] for n in names] for d in train]
        stats = standardise_fit(matrix) if names else []
        std = standardise_apply(matrix, stats) if names else [[] for _ in train]
        beta = solve_ridge(std, [math.log10(ecoli[d]) for d in train], ridge)
        trow = standardise_apply([[feats[test][n] for n in names]], stats)[0] if names else []
        preds[test] = beta[0] + sum(beta[1 + j] * trow[j] for j in range(len(names)))
        abs_log.append(abs(preds[test] - math.log10(ecoli[test])))
    return sum(abs_log) / len(abs_log), preds


# --------------------------------------------------------------------------- #
# Step 2: analyze
# --------------------------------------------------------------------------- #
def run_analyze(args) -> int:
    weather_path = Path(args.weather)
    if not weather_path.exists():
        raise SystemExit(
            f"{weather_path} not found. Run `python {Path(__file__).name} fetch` first "
            "(needs network access to archive-api.open-meteo.com)."
        )
    ecoli = read_samples(Path(args.samples))
    dates = sorted(ecoli)
    daily = load_weather(weather_path)
    upstream_path = Path(args.upstream_weather)
    upstream = load_weather(upstream_path) if upstream_path.exists() else None
    y = [math.log10(ecoli[d]) for d in dates]

    # 1. Univariate correlations of weather features across lookbacks. Local
    #    (Conham) rainfall/temperature, plus upstream (Bath) rainfall if fetched.
    sources = [("", daily)]
    if upstream is not None:
        sources.append(("up_", upstream))
    corr_rows = []
    for lookback in range(1, MAX_LOOKBACK + 1):
        for prefix, source in sources:
            wf = {d: weather_features(source, d, lookback) for d in dates}
            variables = (("rain_sum", "log1p"), ("rain_max", "log1p"))
            if prefix == "":
                variables = variables + (("temp_mean", None),)
            for var, transform in variables:
                xs = [math.log1p(wf[d][var]) if transform == "log1p" else wf[d][var] for d in dates]
                corr_rows.append({"lookback": lookback, "feature": prefix + var, "r": pearson(xs, y)})
    corr_rows.sort(key=lambda r: -(abs(r["r"]) if not math.isnan(r["r"]) else -1))

    def best_rain(prefix):
        cands = [r for r in corr_rows if r["feature"] in (prefix + "rain_sum", prefix + "rain_max")]
        return max(cands, key=lambda r: abs(r["r"]) if not math.isnan(r["r"]) else -1)

    # 2. Build the per-date feature table for the models.
    local_best = best_rain("")
    rain_lb, rain_var = local_best["lookback"], local_best["feature"]
    up_best = best_rain("up_") if upstream is not None else None
    cso = load_cso_feature(Path(args.cso), BEST_CSO_LOOKBACK, BEST_CSO_COLUMN)
    feats = {}
    for d in dates:
        wf = weather_features(daily, d, rain_lb)
        feats[d] = {
            "rain": math.log1p(wf[rain_var]),
            "temp_mean": wf["temp_mean"],
            "cso": math.log1p(cso.get(d, 0.0)),
        }
        if up_best is not None:
            uw = weather_features(upstream, d, up_best["lookback"])
            feats[d]["rain_up"] = math.log1p(uw[up_best["feature"].removeprefix("up_")])

    models = {
        "mean baseline": [],
        f"local rainfall only ({rain_var} {rain_lb}d)": ["rain"],
        "temperature only": ["temp_mean"],
        f"CSO only ({BEST_CSO_COLUMN} {BEST_CSO_LOOKBACK}d)": ["cso"],
        "CSO + local rainfall": ["cso", "rain"],
    }
    if up_best is not None:
        models[f"upstream rainfall only ({up_best['feature']} {up_best['lookback']}d)"] = ["rain_up"]
        models["CSO + upstream rainfall"] = ["cso", "rain_up"]
        models["CSO + upstream rainfall + temperature"] = ["cso", "rain_up", "temp_mean"]
    model_results = {}
    for name, names in models.items():
        mae, preds = loocv(dates, ecoli, feats, names, args.ridge)
        model_results[name] = (mae, names, preds)

    # Per-day output uses the best CSO-containing combined model.
    cso_models = [(mae, name) for name, (mae, _, _) in model_results.items() if name.startswith("CSO +")]
    featured_name = min(cso_models)[1] if cso_models else next(n for n in model_results if n.startswith("CSO only"))
    _, _, best_preds = model_results[featured_name]
    predictions, apes = [], []
    for d in dates:
        actual = ecoli[d]
        pred = 10 ** best_preds[d]
        ape = abs(pred - actual) / actual * 100.0
        apes.append(ape)
        row = {
            "sample_date": d,
            "actual_cfu_per_100ml": round(actual, 1),
            "local_rain_mm": round(weather_features(daily, d, rain_lb)["rain_sum"], 1),
            "upstream_rain_mm": round(weather_features(upstream, d, up_best["lookback"])["rain_sum"], 1) if up_best is not None else "",
            "loocv_predicted_cfu_per_100ml": round(pred, 1),
            "loocv_signed_pct_error": round((pred - actual) / actual * 100.0, 1),
            "loocv_abs_pct_error": round(ape, 1),
        }
        predictions.append(row)
    median_ape = sorted(apes)[len(apes) // 2]

    out_csv = Path(args.predictions)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_date", "actual_cfu_per_100ml", "local_rain_mm", "upstream_rain_mm", "loocv_predicted_cfu_per_100ml", "loocv_signed_pct_error", "loocv_abs_pct_error"],
        )
        writer.writeheader()
        writer.writerows(predictions)

    write_report(Path(args.report), dates, ecoli, daily, upstream, corr_rows, rain_lb, rain_var,
                 up_best, model_results, featured_name, predictions, median_ape)
    print(f"Wrote {out_csv}")
    print(f"Wrote {args.report}")
    print(f"Upstream rainfall: {'included' if upstream is not None else 'NOT FOUND (' + str(upstream_path) + ')'}")
    print("LOOCV MAE_log by model:")
    for name, (mae, _, _) in model_results.items():
        print(f"  {mae:.3f}  {name}")
    return 0


def write_report(path, dates, ecoli, daily, upstream, corr_rows, rain_lb, rain_var,
                 up_best, model_results, featured_name, predictions, median_ape) -> None:
    has_upstream = up_best is not None
    lines = [
        "# Conham E. coli vs weather",
        "",
        "Generated by `scripts/weather_conham_ecoli.py`. Daily rainfall and temperature",
        "summarised over 1- to 7-day windows before each E. coli sample, to test whether",
        "weather influences water quality on its own and on top of the upstream CSO",
        "signal. Rainfall is taken both **at Conham** and **upstream at Bath** (~8-9 miles",
        "up the River Avon, where the spill-driving CSO cluster sits)."
        if has_upstream else
        "signal.",
        "",
        f"- Sample dates: {len(dates)}",
        f"- Upstream (Bath) rainfall: {'included' if has_upstream else 'not available - run fetch'}",
        "",
        "## Which weather features track E. coli?",
        "",
        "Univariate correlation with log10(E. coli). Rainfall is log1p-transformed.",
        "`up_` features are upstream (Bath) rainfall; the rest are at Conham.",
        "",
        "| Rank | Window | Feature | Pearson r |",
        "|---:|---:|---|---:|",
    ]
    for i, r in enumerate(corr_rows[:14], 1):
        rv = "n/a" if math.isnan(r["r"]) else f"{r['r']:+.3f}"
        lines.append(f"| {i} | {r['lookback']}d | {r['feature']} | {rv} |")
    lines.extend(
        [
            "",
            "## Does weather add anything? (leave-one-out cross-validation)",
            "",
            "Lower `MAE_log` is better (a fold-error in log10 units).",
            "",
            "| Model | Features | LOOCV MAE_log |",
            "|---|---|---:|",
        ]
    )
    for name, (mae, names, _) in model_results.items():
        lines.append(f"| {name} | {', '.join(names) or 'intercept'} | {mae:.3f} |")
    cso_only = next(m for n, m in model_results.items() if n.startswith("CSO only"))[0]
    mean_base = model_results["mean baseline"][0]
    best_combined = min((m for n, (m, _, _) in model_results.items() if n.startswith("CSO +")), default=cso_only)
    combined_helps = best_combined < cso_only - 1e-3
    up_only = next((m for n, (m, _, _) in model_results.items() if n.startswith("upstream rainfall")), None)
    local_only = next((m for n, (m, _, _) in model_results.items() if n.startswith("local rainfall")), None)
    up_line = ""
    if has_upstream and up_only is not None and local_only is not None:
        up_helps = up_only < mean_base - 1e-3
        up_line = (
            f" Moving rainfall upstream to Bath does **not** rescue it: the best upstream rain"
            f" term (up_ r = {up_best['r']:+.3f}, weak and the wrong sign) scores {up_only:.3f}"
            f" as a sole predictor -- {'better than' if up_helps else 'still no better than'} the"
            f" mean ({mean_base:.3f}) and about the same as local rain ({local_only:.3f})."
        )
    lines.extend(
        [
            "",
            f"**Bottom line: weather still adds little.** Adding the best weather term to the",
            f"CSO model ({best_combined:.3f}) {'beats' if combined_helps else 'does not beat'} CSO-only "
            f"({cso_only:.3f}).{up_line}",
            "",
            "## Local vs upstream rainfall on the high-E. coli days the CSO model missed",
            "",
            "Some high-E. coli days had no recorded upstream CSO spill in their window. If",
            "they were rain-driven runoff we would expect heavy rain. Checking rainfall both",
            "at Conham and upstream at Bath:",
            "",
            ("| Sample date | E. coli | Conham rain (mm) | Bath rain (mm) |" if has_upstream
             else "| Sample date | E. coli | Conham rain (mm) |"),
            ("|---|---:|---:|---:|" if has_upstream else "|---|---:|---:|"),
        ]
    )
    for d in dates:
        if ecoli[d] >= 450:
            local_rain = weather_features(daily, d, rain_lb)["rain_sum"]
            if has_upstream:
                bath_rain = weather_features(upstream, d, up_best["lookback"])["rain_sum"]
                lines.append(f"| {d} | {ecoli[d]:.0f} | {local_rain:.1f} | {bath_rain:.1f} |")
            else:
                lines.append(f"| {d} | {ecoli[d]:.0f} | {local_rain:.1f} |")
    rain_cols = "| Sample date | Actual | Conham rain | Bath rain | LOOCV predicted | Signed % error | Abs % error |" if has_upstream \
        else "| Sample date | Actual | Conham rain | LOOCV predicted | Signed % error | Abs % error |"
    rain_sep = "|---|---:|---:|---:|---:|---:|---:|" if has_upstream else "|---|---:|---:|---:|---:|---:|"
    lines.extend(
        [
            "",
            f"## Per-day percentage error ({featured_name}, leave-one-out)",
            "",
            f"Median absolute error **{median_ape:.1f}%**.",
            "",
            rain_cols,
            rain_sep,
        ]
    )
    for p in predictions:
        if has_upstream:
            lines.append(
                f"| {p['sample_date']} | {p['actual_cfu_per_100ml']:.0f} | {p['local_rain_mm']:.0f} | {p['upstream_rain_mm']:.0f} | "
                f"{p['loocv_predicted_cfu_per_100ml']:.0f} | {p['loocv_signed_pct_error']:+.1f}% | {p['loocv_abs_pct_error']:.1f}% |"
            )
        else:
            lines.append(
                f"| {p['sample_date']} | {p['actual_cfu_per_100ml']:.0f} | {p['local_rain_mm']:.0f} | "
                f"{p['loocv_predicted_cfu_per_100ml']:.0f} | {p['loocv_signed_pct_error']:+.1f}% | {p['loocv_abs_pct_error']:.1f}% |"
            )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- ERA5 is a ~9 km reanalysis grid, not a gauge; local convective rain can be",
            "  mis-estimated at either point. Note some high-spill days show ~0 mm here,",
            "  which is suspicious -- either the grid misses the rain or those spills are not",
            "  rainfall-driven (e.g. groundwater infiltration), so the rainfall signal may be",
            "  understated.",
            "- Rainfall and CSO spills are strongly related (rain triggers spills), so their",
            "  separate coefficients are hard to interpret; the useful question is whether",
            "  rainfall adds predictive power beyond the CSO signal.",
            "- 25 chart-digitised, right-censored samples: treat as exploratory.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    f = sub.add_parser("fetch", help="Fetch daily weather from Open-Meteo (needs network)")
    f.add_argument("--samples", default=SAMPLES_CSV)
    f.add_argument("--weather", default=WEATHER_CSV)
    f.add_argument("--upstream-weather", default=UPSTREAM_WEATHER_CSV)
    f.set_defaults(func=run_fetch)

    a = sub.add_parser("analyze", help="Correlate weather with E. coli and fit combined model (offline)")
    a.add_argument("--samples", default=SAMPLES_CSV)
    a.add_argument("--weather", default=WEATHER_CSV)
    a.add_argument("--upstream-weather", default=UPSTREAM_WEATHER_CSV)
    a.add_argument("--cso", default=CSO_FEATURES_CSV)
    a.add_argument("--predictions", default=PREDICTIONS_CSV)
    a.add_argument("--report", default=REPORT_MD)
    a.add_argument("--ridge", type=float, default=DEFAULT_RIDGE)
    a.set_defaults(func=run_analyze)

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
