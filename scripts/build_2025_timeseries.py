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
from datetime import date, timedelta
from pathlib import Path

WEATHER = "docs/data/conham_weather_daily.csv"
FEATURES = "docs/data/conham_cso_ecoli_features.csv"
INTENSITY = "docs/data/rainfall_intensity_daily_max.csv"
OUTPUT = "docs/data/conham_2025_timeseries.csv"


def main() -> int:
    weather = {}
    with open(WEATHER, newline="", encoding="utf-8") as h:
        for r in csv.DictReader(h):
            weather[r["date"]] = r
    # E. coli plus CSO spill hours at several lookback windows per sample date:
    # same day (1), 2 days and 7 days before the sample.
    samples = {}
    with open(FEATURES, newline="", encoding="utf-8") as h:
        for r in csv.DictReader(h):
            lb = int(r["lookback_days"])
            if lb not in (1, 2, 7):
                continue
            s = samples.setdefault(
                r["sample_date"], {"ecoli": r["e_coli_cfu_per_100ml"]})
            s[f"cso{lb}"] = r["spill_hours_total"]

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
        hrs = lambda v: (f"{float(v):.1f}" if v not in (None, "") else "")
        rows.append({
            "date": key,
            "ecoli_cfu_per_100ml": s.get("ecoli", ""),
            "cso_spill_hours_sameday": hrs(s.get("cso1")),
            "cso_spill_hours_2d": hrs(s.get("cso2")),
            "cso_spill_hours_7d": hrs(s.get("cso7")),
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
    print(f"Wrote {out} ({len(rows)} days, {n_samples} sample dates)")
    print(f"  wind data present: {has_wind}")
    print(f"  CAPE data present: {has_cape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
