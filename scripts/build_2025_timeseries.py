#!/usr/bin/env python3
"""Merge the 2025 Conham series into one CSV for plotting.

Combines, on a daily calendar for 2025:
- E. coli (CFU/100ml) and CSO amount (spill hours in the prior 7 days) at each
  weekly sample date, from ``docs/data/conham_cso_ecoli_features.csv``;
- daily rainfall, mean temperature and (if fetched) max wind speed, from
  ``docs/data/conham_weather_daily.csv``;
- the catchment-wide daily peak CAPE (thunderstorm instability, J/kg) from
  ``docs/data/rainfall_intensity_daily_max.csv`` if present.

E. coli and CSO columns are populated only on sample dates (blank otherwise) so
they plot as weekly markers over the daily weather lines. Standard library only.
"""
from __future__ import annotations

import csv
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

WEATHER = "docs/data/conham_weather_daily.csv"
FEATURES = "docs/data/conham_cso_ecoli_features.csv"
SAMPLING = "docs/data/conham_sampling_2025_2026.csv"
DAILY_CSO = "docs/data/conham_cso_daily.csv"
INTENSITY = "docs/data/rainfall_intensity_daily_max.csv"
OUTPUT = "docs/data/conham_2025_timeseries.csv"

# UK Bathing Water Directive (2006/7/EC) classification, INLAND waters. Classes
# are decided from log-normal percentiles of the sample set: the 95-percentile
# (z=1.65) for Excellent/Good and the 90-percentile (z=1.282) for Sufficient.
CLASS_RANK = {0: "excellent", 1: "good", 2: "sufficient", 3: "poor"}
MIN_SAMPLES_TO_CLASSIFY = 5  # percentiles are meaningless with too few points


def _percentile(values: list[float], z: float) -> float:
    """Log-normal percentile used by the Directive: antilog(mean + z*sd)."""
    logs = [math.log10(v) for v in values]
    return 10 ** (statistics.fmean(logs) + z * statistics.stdev(logs))


def bathing_class(ec: list[float], ent: list[float]) -> int:
    """Worse (higher rank) of the E. coli and enterococci classes; 0=Excellent..3=Poor."""
    ec95, ec90 = _percentile(ec, 1.65), _percentile(ec, 1.282)
    en95, en90 = _percentile(ent, 1.65), _percentile(ent, 1.282)
    ec_c = 0 if ec95 <= 500 else 1 if ec95 <= 1000 else 2 if ec90 <= 900 else 3
    en_c = 0 if en95 <= 200 else 1 if en95 <= 400 else 2 if en90 <= 330 else 3
    return max(ec_c, en_c)


def main() -> int:
    weather = {}
    with open(WEATHER, newline="", encoding="utf-8") as h:
        for r in csv.DictReader(h):
            weather[r["date"]] = r
    # E. coli per sample date (always from the feature table).
    samples = {}
    with open(FEATURES, newline="", encoding="utf-8") as h:
        for r in csv.DictReader(h):
            if int(r["lookback_days"]) == 7:
                samples[r["sample_date"]] = {"ecoli": r["e_coli_cfu_per_100ml"]}

    # Intestinal enterococci per sample date, from the combined sampling CSV.
    entero = {}
    sampling_path = Path(SAMPLING)
    if sampling_path.exists():
        with sampling_path.open(newline="", encoding="utf-8") as h:
            for r in csv.DictReader(h):
                entero[r["sample_date"]] = r.get("intestinal_enterococci_cfu_per_100ml", "")

    # UK bathing-water class AS OF each sample date, from the percentiles of all
    # samples up to and including it (expanding window -- the causal "rating so
    # far"). Takes the worse of the E. coli and enterococci classes.
    classes = {}
    ec_hist, en_hist = [], []
    for sd in sorted(samples):
        try:
            ec_hist.append(float(samples[sd]["ecoli"]))
            en_hist.append(float(entero.get(sd, "")))
        except ValueError:
            continue
        if len(ec_hist) >= MIN_SAMPLES_TO_CLASSIFY:
            classes[sd] = CLASS_RANK[bathing_class(ec_hist, en_hist)]

    # CSO spill hours. Prefer the continuous DAILY series (conham_cso_daily.csv,
    # from daily_cso.py) so the CSO panels are populated every day; fall back to
    # the sample-only feature windows if the daily fetch hasn't been run.
    daily_cso = {}
    daily_path = Path(DAILY_CSO)
    if daily_path.exists():
        with daily_path.open(newline="", encoding="utf-8") as h:
            for r in csv.DictReader(h):
                daily_cso[r["date"]] = (
                    r["spill_hours_day"], r["spill_hours_2d"], r["spill_hours_7d"])
    else:
        # Sample-only fallback: same-day (lookback 1), 2-day and 7-day windows.
        with open(FEATURES, newline="", encoding="utf-8") as h:
            per_sample = {}
            for r in csv.DictReader(h):
                lb = int(r["lookback_days"])
                if lb in (1, 2, 7):
                    per_sample.setdefault(r["sample_date"], {})[lb] = r["spill_hours_total"]
        for sd, bylb in per_sample.items():
            daily_cso[sd] = (bylb.get(1, ""), bylb.get(2, ""), bylb.get(7, ""))

    # Catchment-wide daily peak CAPE and peak rainfall intensity (optional; blank
    # if the intensity fetch hasn't been run/committed).
    cape, peak_rain = {}, {}
    intensity_path = Path(INTENSITY)
    if intensity_path.exists():
        with intensity_path.open(newline="", encoding="utf-8") as h:
            for r in csv.DictReader(h):
                cape[r["date"]] = r.get("catchment_max_cape_j_per_kg", "")
                peak_rain[r["date"]] = r.get("catchment_max_mm_per_h", "")

    days = sorted(d for d in weather if d.startswith("2025"))
    start, end = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    rows = []
    d = start
    while d <= end:
        key = d.isoformat()
        w = weather.get(key, {})
        s = samples.get(key, {})
        cso = daily_cso.get(key, ("", "", ""))
        hrs = lambda v: (f"{float(v):.1f}" if v not in (None, "") else "")
        rows.append({
            "date": key,
            "ecoli_cfu_per_100ml": s.get("ecoli", ""),
            "intestinal_enterococci_cfu_per_100ml": entero.get(key, ""),
            "bathing_class": classes.get(key, ""),
            "cso_spill_hours_sameday": hrs(cso[0]),
            "cso_spill_hours_2d": hrs(cso[1]),
            "cso_spill_hours_7d": hrs(cso[2]),
            "rain_mm": w.get("precipitation_mm", ""),
            "peak_rain_mm_per_h": peak_rain.get(key, ""),
            "temp_mean_c": w.get("temp_mean_c", ""),
            # Populated once wind is added to the weather fetch (blank until then).
            "wind_max_kmh": w.get("windspeed_10m_max_kmh", w.get("wind_max_kmh", "")),
            "cape_max_j_per_kg": cape.get(key, ""),
        })
        d += timedelta(days=1)

    out = Path(OUTPUT)
    with out.open("w", newline="", encoding="utf-8") as h:
        writer = csv.DictWriter(h, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    n_samples = sum(1 for r in rows if r["ecoli_cfu_per_100ml"])
    has_wind = any(r["wind_max_kmh"] for r in rows)
    has_cape = any(r["cape_max_j_per_kg"] for r in rows)
    cso_days = sum(1 for r in rows if r["cso_spill_hours_7d"] != "")
    print(f"Wrote {out} ({len(rows)} days, {n_samples} sample dates)")
    print(f"  CSO days populated: {cso_days} ({'daily series' if daily_path.exists() else 'sample-only fallback'})")
    print(f"  wind data present: {has_wind}")
    print(f"  CAPE data present: {has_cape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
