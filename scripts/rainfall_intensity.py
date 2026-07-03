#!/usr/bin/env python3
"""Rainfall *intensity* (max mm/hour within a day) across the Bristol Avon catchment.

Daily rainfall totals hide the thing that actually triggers a first-flush CSO
spill or a flashy runoff pulse: a short, violent burst. 15 mm falling over an
hour is a very different event from 15 mm drizzled over a day. Convective
thunderstorms are also *local* -- a cell can dump 20 mm on Keynsham while Conham
stays dry -- so a single point (as in `weather_conham_ecoli.py`) can miss them
entirely.

This script pulls **hourly** precipitation, **CAPE** and (best-effort)
**lightning potential** for many sites spread across the Bristol Avon catchment
(Bristol, Bath, the Chew and Frome sub-catchments, and the upper Avon
headwaters) and, for each site and day, computes:

- ``rain_total_mm``      -- daily total (sanity check against the daily archive);
- ``rain_max_mm_per_h``  -- the heaviest single hour = peak intensity;
- ``peak_hour``          -- the local hour (0-23) that peak fell in;
- ``cape_max``           -- the day's peak CAPE (instability "fuel", J/kg);
- ``cape_at_peak_hour``  -- CAPE during the heaviest rain hour;
- ``lightning_potential_max`` -- the day's peak modelled lightning index (LPI).

CAPE (Convective Available Potential Energy) is a thunderstorm-likelihood proxy:
a heavy rain hour landing on a high-CAPE day (say >~500 J/kg) is strong evidence
the downpour was **convective** -- a storm cell -- rather than gentle frontal
rain. That is exactly the localised-thunderstorm signal we are chasing.

Data sources (two endpoints -- this matters). Precipitation comes from the ERA5
reanalysis **archive** (``archive-api.open-meteo.com``), which is consistent and
good for rainfall. But that archive is a surface/land dataset and carries **no**
CAPE or lightning -- requesting ``hourly=cape`` there returns all-null (silently
written as 0.0, the original bug). CAPE and ``lightning_potential`` live in the
**Historical Forecast API** (``historical-forecast-api.open-meteo.com``), which
replays past high-resolution forecast runs, so they are fetched there and merged
by timestamp. lightning_potential is strictly best-effort: only some models
produce it and UK coverage isn't guaranteed, so its column may be blank.

Two outputs:

- ``docs/data/rainfall_intensity_by_site.csv`` -- tidy long form (date, site,
  lat, lon, rain_total_mm, rain_max_mm_per_h, peak_hour, cape_max_j_per_kg,
  cape_at_peak_hour_j_per_kg, lightning_potential_max);
- ``docs/data/rainfall_intensity_daily_max.csv`` -- wide: one row per day, one
  column per site of the peak hourly intensity, plus catchment-wide summaries of
  the worst downpour, the highest CAPE and the highest lightning potential (each
  with the site it occurred at).

Like the other fetch scripts, the network step is separate because it needs
outbound access to ``archive-api.open-meteo.com`` and
``historical-forecast-api.open-meteo.com``:

    python scripts/rainfall_intensity.py fetch     # -> the two CSVs above
    python scripts/rainfall_intensity.py sites      # just list the sites, no network

``fetch`` covers the same date range as the E. coli sampling programme by
default (min sample date minus a buffer .. max sample date); override with
``--start`` / ``--end``. Caveats: these are ~2-11 km model grids, not rain
gauges, and they *smooth* the sharpest convective peaks, so treat the intensity
as a lower bound and all fields as a relative (site-to-site, day-to-day) signal
rather than absolute readings.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# CAPE and lightning_potential are NOT in the ERA5 reanalysis archive (that is a
# surface/land dataset). They live in the Historical Forecast API, which replays
# past runs of the high-resolution forecast models and reaches back to 2022.
FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

SAMPLES_CSV = "docs/data/conham_sampling_2025_2026_e_coli.csv"
LONG_CSV = "docs/data/rainfall_intensity_by_site.csv"
WIDE_CSV = "docs/data/rainfall_intensity_daily_max.csv"
BUFFER_DAYS = 8  # extend the window before the first sample, matching the weather script

# Sites spread across the Bristol Avon catchment. The aim is spatial coverage so
# a localised thunderstorm cell shows up somewhere, not a dense grid. (name, lat, lon)
SITES: list[tuple[str, float, float]] = [
    # --- Conham and the immediate Bristol / lower-Avon reach ---
    ("Conham", 51.4449, -2.5348),
    ("Hanham", 51.4400, -2.5150),
    ("Bristol (centre)", 51.4545, -2.5879),
    ("Kingswood", 51.4550, -2.5060),
    ("Keynsham", 51.4137, -2.4967),
    ("Bristol Frome (Frenchay)", 51.5060, -2.5320),
    ("Bradley Stoke", 51.5340, -2.5410),
    ("Portishead (tidal mouth)", 51.4840, -2.7620),
    ("Nailsea", 51.4300, -2.7600),
    # --- River Chew sub-catchment (south-west) ---
    ("Pensford (Chew)", 51.3700, -2.5500),
    ("Chew Magna", 51.3620, -2.6180),
    ("Blagdon (Chew head)", 51.3280, -2.7150),
    # --- Bristol Frome / Ladden (north) ---
    ("Yate", 51.5410, -2.4160),
    ("Thornbury", 51.6100, -2.5250),
    ("Wotton-under-Edge", 51.6380, -2.3490),
    # --- Bath and the By Brook / eastern reach ---
    ("Bath", 51.3800, -2.3590),
    ("Marshfield (By Brook)", 51.4620, -2.3080),
    ("Colerne", 51.4400, -2.2830),
    ("Corsham", 51.4340, -2.1870),
    ("Bradford-on-Avon", 51.3467, -2.2513),
    # --- Southern tributaries: Midford / Wellow / Somer / Somerset Frome ---
    ("Radstock (Wellow Brook)", 51.2915, -2.4470),
    ("Midsomer Norton (Somer)", 51.2830, -2.4820),
    ("Frome (Somerset)", 51.2286, -2.3215),
    ("Westbury", 51.2610, -2.1880),
    ("Trowbridge", 51.3200, -2.2087),
    ("Melksham", 51.3736, -2.1387),
    # --- Upper Avon headwaters (east / north-east) ---
    ("Chippenham", 51.4585, -2.1155),
    ("Calne (Marden)", 51.4380, -2.0060),
    ("Devizes", 51.3520, -1.9950),
    ("Malmesbury (headwaters)", 51.5843, -2.0994),
    ("Tetbury (source)", 51.6383, -2.1608),
]


def read_sample_dates(path: Path) -> list[date]:
    with path.open(newline="", encoding="utf-8") as handle:
        return sorted(date.fromisoformat(row["sample_date"]) for row in csv.DictReader(handle))


def _request_hourly(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=120) as response:
        data = json.load(response)
    if data.get("error"):
        raise RuntimeError(json.dumps(data, indent=2))
    return data.get("hourly", {})


def _optional_hourly_map(url: str, base: dict, variable: str) -> dict[str, float]:
    """{timestamp: value} for a best-effort variable; {} if the API can't serve it.

    Used for CAPE and lightning_potential, which some models/regions don't
    produce. A failure here must not sink the whole fetch, so errors and nulls
    are swallowed and simply yield an empty map (the caller then warns).
    """
    try:
        hourly = _request_hourly(url, {**base, "hourly": variable})
    except (urllib.error.URLError, RuntimeError):
        return {}
    times = hourly.get("time", [])
    vals = hourly.get(variable, [None] * len(times))
    return {t: v for t, v in zip(times, vals) if v is not None}


def fetch_hourly(lat: float, lon: float, start: date, end: date):
    """Return (rows, n_cape_present, n_lightning_present).

    ``rows`` is [(iso_hour, precipitation_mm, cape_j_per_kg, lightning_potential), ...].

    Precipitation comes from the ERA5 **archive** (reanalysis) -- consistent and
    good for localised-cell intensity, which is the whole point of the script.
    But the archive is a surface/land dataset and carries **no** CAPE or
    lightning (an ``hourly=cape`` request there returns all-null, silently
    written as 0.0 -- the original bug). Those convective fields live in the
    Historical Forecast API instead, so they are fetched from ``FORECAST_URL`` and
    merged back by timestamp:

    - ``cape`` (Convective Available Potential Energy, J/kg): instability "fuel";
      a heavy rain hour on a high-CAPE day is likely a convective storm cell.
    - ``lightning_potential`` (LPI, J/kg): a modelled lightning-likelihood index.
      Only some models produce it and UK coverage is not guaranteed, so it is
      strictly best-effort -- if the API can't serve it the column is left blank.

    The two ``n_*_present`` counts let the caller warn loudly (rather than write
    silent zeros) if either convective field comes back empty.
    """
    base = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "Europe/London",
    }
    precip_h = _request_hourly(ARCHIVE_URL, {**base, "hourly": "precipitation"})
    cape_by_hour = _optional_hourly_map(FORECAST_URL, base, "cape")
    lightning_by_hour = _optional_hourly_map(FORECAST_URL, base, "lightning_potential")

    times = precip_h.get("time", [])
    precip = precip_h.get("precipitation", [None] * len(times))

    rows = []
    n_cape = n_light = 0
    for t, p in zip(times, precip):
        c = cape_by_hour.get(t)
        li = lightning_by_hour.get(t)
        if c is not None:
            n_cape += 1
        if li is not None:
            n_light += 1
        rows.append((t, (p if p is not None else 0.0),
                     (c if c is not None else 0.0), (li if li is not None else 0.0)))
    return rows, n_cape, n_light


# Per-day tuple: (total_mm, peak_mm_per_h, peak_hour, cape_max, cape_at_peak_hour, lightning_max).
DayStats = tuple[float, float, int, float, float, float]


def daily_intensity(hourly) -> dict[str, DayStats]:
    """Collapse hourly rain + CAPE + lightning to per-day stats.

    Returns, per day: daily total rain, the heaviest single hour (peak
    intensity) and the hour it fell in, the day's max CAPE, the CAPE during that
    heaviest rain hour (ties instability to the actual downpour), and the day's
    max lightning-potential index.
    """
    by_day: dict[str, list[tuple[int, float, float, float]]] = defaultdict(list)
    for iso_hour, mm, cape, light in hourly:
        day, _, clock = iso_hour.partition("T")
        hour = int(clock[:2]) if clock else 0
        by_day[day].append((hour, mm, cape, light))
    out: dict[str, DayStats] = {}
    for day, hours in by_day.items():
        total = sum(mm for _, mm, _, _ in hours)
        peak_hour, peak_mm, cape_at_peak, _ = max(hours, key=lambda h: h[1])
        cape_max = max(c for _, _, c, _ in hours)
        light_max = max(li for _, _, _, li in hours)
        out[day] = (round(total, 2), round(peak_mm, 2), peak_hour,
                    round(cape_max, 1), round(cape_at_peak, 1), round(light_max, 2))
    return out


def run_fetch(args) -> int:
    if args.start and args.end:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    else:
        dates = read_sample_dates(Path(args.samples))
        start = min(dates) - timedelta(days=BUFFER_DAYS)
        end = max(dates)
    print(f"Fetching hourly rainfall for {len(SITES)} sites, {start}..{end}")

    # site -> {day -> DayStats}
    per_site: dict[str, dict[str, DayStats]] = {}
    cape_present_total = light_present_total = 0
    for i, (name, lat, lon) in enumerate(SITES, 1):
        try:
            hourly, n_cape, n_light = fetch_hourly(lat, lon, start, end)
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"Could not reach Open-Meteo ({ARCHIVE_URL}): {exc}.\n"
                "Run `fetch` where archive-api.open-meteo.com egress is allowed, then commit\n"
                f"  {LONG_CSV}\n  {WIDE_CSV}"
            )
        cape_present_total += n_cape
        light_present_total += n_light
        per_site[name] = daily_intensity(hourly)
        cape = "no CAPE!" if n_cape == 0 else f"CAPE {n_cape}h"
        light = "no LPI" if n_light == 0 else f"LPI {n_light}h"
        print(f"  [{i:>2}/{len(SITES)}] {name}: {len(per_site[name])} days, {cape}, {light}")
        if i < len(SITES):
            time.sleep(0.5)  # be polite to the free API

    all_days = sorted({d for days in per_site.values() for d in days})

    if cape_present_total == 0:
        print("\n  WARNING: CAPE came back empty for every site -- the Historical Forecast\n"
              f"  API ({FORECAST_URL}) served no `cape`. Do NOT trust the cape_* columns.\n")
    if light_present_total == 0:
        print("  NOTE: lightning_potential (LPI) was empty for every site -- the model\n"
              "  covering this area doesn't produce it. The lightning columns are blank.\n")

    # Long / tidy form.
    long_path = Path(args.long)
    long_path.parent.mkdir(parents=True, exist_ok=True)
    coords = {name: (lat, lon) for name, lat, lon in SITES}
    with long_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "site", "lat", "lon", "rain_total_mm", "rain_max_mm_per_h",
                         "peak_hour", "cape_max_j_per_kg", "cape_at_peak_hour_j_per_kg",
                         "lightning_potential_max"])
        for name, _, _ in SITES:
            lat, lon = coords[name]
            for day in all_days:
                if day in per_site[name]:
                    total, peak_mm, peak_hour, cape_max, cape_at_peak, light_max = per_site[name][day]
                    writer.writerow([day, name, lat, lon, total, peak_mm, peak_hour,
                                     cape_max, cape_at_peak, light_max])

    # Wide form: peak hourly intensity per site per day + catchment-wide worst,
    # plus a catchment-wide CAPE summary so a day's convective potential sits
    # next to its heaviest downpour.
    site_names = [name for name, _, _ in SITES]
    wide_path = Path(args.wide)
    with wide_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date"] + site_names + [
            "catchment_max_mm_per_h", "catchment_max_site",
            "catchment_max_cape_j_per_kg", "catchment_max_cape_site",
            "catchment_max_lightning_potential", "catchment_max_lightning_site"])
        for day in all_days:
            row = [day]
            best_mm, best_site = -1.0, ""
            best_cape, best_cape_site = -1.0, ""
            best_light, best_light_site = -1.0, ""
            for name in site_names:
                if day in per_site[name]:
                    peak_mm = per_site[name][day][1]
                    cape_max = per_site[name][day][3]
                    light_max = per_site[name][day][5]
                    row.append(peak_mm)
                    if peak_mm > best_mm:
                        best_mm, best_site = peak_mm, name
                    if cape_max > best_cape:
                        best_cape, best_cape_site = cape_max, name
                    if light_max > best_light:
                        best_light, best_light_site = light_max, name
                else:
                    row.append("")
            row.extend([
                round(best_mm, 2) if best_mm >= 0 else "", best_site,
                round(best_cape, 1) if best_cape >= 0 else "", best_cape_site,
                round(best_light, 2) if best_light >= 0 else "", best_light_site])
            writer.writerow(row)

    print(f"Wrote {long_path} ({len(all_days) * len(SITES)} site-days)")
    print(f"Wrote {wide_path} ({len(all_days)} days x {len(SITES)} sites)")
    # Quick headline: the ten most intense downpours anywhere in the catchment,
    # with the CAPE at that hour (a rough convective flag: >~500 J/kg is a
    # thunderstorm-favourable atmosphere).
    peaks = []
    for day in all_days:
        for name in site_names:
            if day in per_site[name]:
                total, peak_mm, peak_hour, cape_max, cape_at_peak, light_max = per_site[name][day]
                peaks.append((peak_mm, day, name, peak_hour, cape_at_peak))
    peaks.sort(reverse=True)
    print("Ten most intense single hours in the catchment (with CAPE that hour):")
    for mm, day, name, hr, cape in peaks[:10]:
        flag = " <- convective" if cape >= 500 else ""
        print(f"  {mm:6.1f} mm/h  {day} {hr:02d}:00  {name:<28} CAPE {cape:6.0f} J/kg{flag}")
    return 0


def run_sites(args) -> int:
    print(f"{len(SITES)} sites across the Bristol Avon catchment:")
    for name, lat, lon in SITES:
        print(f"  {name:<28} {lat:.4f}, {lon:.4f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    f = sub.add_parser("fetch", help="Fetch hourly rainfall and derive daily intensity (needs network)")
    f.add_argument("--samples", default=SAMPLES_CSV)
    f.add_argument("--long", default=LONG_CSV)
    f.add_argument("--wide", default=WIDE_CSV)
    f.add_argument("--start", help="ISO date; defaults to first sample date minus a buffer")
    f.add_argument("--end", help="ISO date; defaults to last sample date")
    f.set_defaults(func=run_fetch)

    s = sub.add_parser("sites", help="List the catchment sites (no network)")
    s.set_defaults(func=run_sites)

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
