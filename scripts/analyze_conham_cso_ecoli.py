#!/usr/bin/env python3
"""Analyse whether upstream CSO activity is associated with Conham E. coli samples.

Uses the Wessex Water Event Duration Monitoring 2025 ArcGIS FeatureServer with
the Conham watercourse query logic from ``poo.py``. For every E. coli sample date
in the CSV, it queries CSO
activity in 1- to 7-day lookback windows ending at the sample date, summarises
spill duration by distance band, and fits simple one-variable OLS models against
log10(E. coli CFU/100ml).

The script intentionally uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Iterable

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
BANDS = [(0, 1, "within_1_mile"), (1, 5, "1_to_5_miles"), (5, 10, "5_to_10_miles"), (10, 20, "10_to_20_miles"), (20, 50, "20_to_50_miles")]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="docs/data/conham_sampling_2025_2026_e_coli.csv", help="E. coli sampling CSV")
    parser.add_argument("--summary-csv", default="docs/data/conham_cso_ecoli_features.csv", help="Output CSV of sample-window CSO features")
    parser.add_argument("--report", default="docs/data/conham_cso_ecoli_analysis.md", help="Output markdown report")
    parser.add_argument("--sleep", type=float, default=0.1, help="Delay between ArcGIS page requests")
    parser.add_argument("--page-size", type=int, default=2000, help="ArcGIS records to request per page")
    return parser.parse_args()


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def ms_to_datetime(value: int | float | None) -> datetime | None:
    # ArcGIS date fields are epoch milliseconds. Some feeds use 0 for
    # open/unknown end times, so treat both None and 0 as missing.
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
            "value_note": row.get("value_note", ""),
        }
        for row in rows
    ]


def arcgis_where(start: datetime, end: datetime) -> str:
    river_clause = " OR ".join(f"ReceivingWatercourse = '{river}'" for river in CONHAM_RIVERS)
    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_s = end.strftime("%Y-%m-%d %H:%M:%S")
    return f"({river_clause}) AND EventStart >= DATE '{start_s}' AND EventStart < DATE '{end_s}'"


def fetch_page(params: dict[str, str]) -> dict[str, object]:
    url = ARCGIS_QUERY_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    if "error" in data:
        raise RuntimeError(json.dumps(data["error"], indent=2))
    return data


def fetch_features(start: datetime, end: datetime, page_size: int, sleep_seconds: float) -> list[dict[str, object]]:
    # ArcGIS services cap a single query response. Page through the result set so
    # multi-day windows do not silently miss older records after the first page.
    features: list[dict[str, object]] = []
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
        data = fetch_page(params)
        page = data.get("features", [])
        if not isinstance(page, list):
            raise RuntimeError("ArcGIS response did not contain a feature list")
        features.extend(page)
        if len(page) < page_size or not data.get("exceededTransferLimit"):
            break
        offset += page_size
        time.sleep(sleep_seconds)
    return features


def summarise_window(features: Iterable[dict[str, object]], start: datetime, end: datetime) -> dict[str, float | int | str]:
    summary: dict[str, float | int | str] = {f"spill_hours_{label}": 0.0 for _, _, label in BANDS}
    summary.update({"queried_feature_count": 0, "event_count": 0, "spill_hours_total": 0.0, "nearest_spill_miles": ""})
    nearest: float | None = None
    seen: set[tuple[object, object, object]] = set()
    for feature in features:
        summary["queried_feature_count"] = int(summary["queried_feature_count"]) + 1
        attrs = feature.get("attributes", {})  # type: ignore[assignment]
        lat = attrs.get("OutfallLatitude")
        lon = attrs.get("OutfallLongitude")
        event_start = ms_to_datetime(attrs.get("EventStart"))
        event_end = ms_to_datetime(attrs.get("EventEnd"))
        if lat is None or lon is None or event_start is None or event_end is None:
            continue
        key = (attrs.get("EventId") or attrs.get("SiteId"), attrs.get("EventStart"), attrs.get("EventEnd"))
        if key in seen:
            continue
        seen.add(key)
        duration_hours = (event_end - event_start).total_seconds() / 3600
        distance = haversine(CONHAM_LAT, CONHAM_LON, float(lat), float(lon))
        nearest = distance if nearest is None else min(nearest, distance)
        summary["event_count"] = int(summary["event_count"]) + 1
        summary["spill_hours_total"] = float(summary["spill_hours_total"]) + duration_hours
        for lower, upper, label in BANDS:
            if lower < distance <= upper:
                summary[f"spill_hours_{label}"] = float(summary[f"spill_hours_{label}"]) + duration_hours
                break
    if nearest is not None:
        summary["nearest_spill_miles"] = round(nearest, 3)
    return summary


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def ranks(values: list[float]) -> list[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    out = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        rank = (i + j + 2) / 2
        for _, index in ordered[i : j + 1]:
            out[index] = rank
        i = j + 1
    return out


def model_table(rows: list[dict[str, object]]) -> list[dict[str, float | int | str]]:
    candidates = ["event_count", "spill_hours_total", "spill_hours_within_1_mile", "spill_hours_1_to_5_miles", "spill_hours_5_to_10_miles", "spill_hours_10_to_20_miles", "spill_hours_20_to_50_miles"]
    results = []
    for lag in range(1, 8):
        lag_rows = [r for r in rows if r["lookback_days"] == lag]
        y = [math.log10(float(r["e_coli_cfu_per_100ml"])) for r in lag_rows]
        for candidate in candidates:
            x = [math.log1p(float(r[candidate])) for r in lag_rows]
            r_value = pearson(x, y)
            rho = pearson(ranks(x), ranks(y))
            results.append({"lookback_days": lag, "feature": candidate, "n": len(lag_rows), "pearson_r": r_value, "r_squared": r_value * r_value if not math.isnan(r_value) else float("nan"), "spearman_rho": rho})
    # Sort by descending R². R² lies in [0, 1], so -R² lies in [-1, 0]; use a
    # positive sentinel for undefined (NaN) correlations so degenerate features
    # with no variation sort to the bottom instead of tying with a perfect fit.
    return sorted(results, key=lambda r: (1.0 if math.isnan(float(r["r_squared"])) else -float(r["r_squared"]), r["lookback_days"], str(r["feature"])))


def write_report(path: Path, rows: list[dict[str, object]], models: list[dict[str, object]]) -> None:
    top = models[:10]
    lines = [
        "# Conham CSO / E. coli exploratory analysis",
        "",
        "This report is generated from `scripts/analyze_conham_cso_ecoli.py` using the Wessex Water 2025 Event Duration Monitoring ArcGIS dataset and the Conham E. coli sampling CSV.",
        "",
        f"Sample dates analysed: {len({r['sample_date'] for r in rows})}",
        "",
        "## Best one-variable log-linear associations",
        "",
        "| Rank | Lookback days | Feature | n | Pearson r | R^2 | Spearman rho |",
        "|---:|---:|---|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(top, 1):
        lines.append(f"| {i} | {row['lookback_days']} | `{row['feature']}` | {row['n']} | {float(row['pearson_r']):.3f} | {float(row['r_squared']):.3f} | {float(row['spearman_rho']):.3f} |")
    lines.extend([
        "",
        "## Interpretation cautions",
        "",
        "- The E. coli values are chart-digitised approximations and capped values at 1000 CFU/100ml are right-censored.",
        "- The CSO query uses Wessex Water's static 2025 Event Duration Monitoring dataset, bounded to the selected 1- to 7-day lookback window before each sample.",
        "- These are simple exploratory correlations, not causal models. Rainfall, river flow, sunlight, temperature, sample time, and travel time are not controlled here.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    samples = read_samples(Path(args.input))
    rows: list[dict[str, object]] = []
    for sample in samples:
        sample_end = datetime.combine(sample["sample_date"], dt_time.min, tzinfo=timezone.utc)  # type: ignore[arg-type]
        for lookback in range(1, 8):
            window_start = sample_end - timedelta(days=lookback)
            features = fetch_features(window_start, sample_end, args.page_size, args.sleep)
            summary = summarise_window(features, window_start, sample_end)
            rows.append({**sample, "lookback_days": lookback, **summary})
            time.sleep(args.sleep)
    out_csv = Path(args.summary_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    models = model_table(rows)
    write_report(Path(args.report), rows, models)
    print(f"Wrote {out_csv}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
