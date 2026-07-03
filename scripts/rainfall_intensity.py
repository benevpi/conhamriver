#!/usr/bin/env python3
"""Rainfall *intensity* (max mm/hour within a day) across the Bristol Avon catchment.

Daily rainfall totals hide the thing that actually triggers a first-flush CSO
spill or a flashy runoff pulse: a short, violent burst. 15 mm falling over an
hour is a very different event from 15 mm drizzled over a day. Convective
thunderstorms are also *local* -- a cell can dump 20 mm on Keynsham while Conham
stays dry -- so a single point (as in `weather_conham_ecoli.py`) can miss them
entirely.

This script pulls **hourly** precipitation and **CAPE** from the Open-Meteo ERA5
archive for many sites spread across the Bristol Avon catchment (Bristol, Bath,
the Chew and Frome sub-catchments, and the upper Avon headwaters) and, for each
site and day, computes:

- ``rain_total_mm``      -- daily total (sanity check against the daily archive);
- ``rain_max_mm_per_h``  -- the heaviest single hour = peak intensity;
- ``peak_hour``          -- the local hour (0-23) that peak fell in;
- ``cape_max``           -- the day's peak CAPE (instability "fuel", J/kg);
- ``cape_at_peak_hour``  -- CAPE during the heaviest rain hour.

CAPE (Convective Available Potential Energy) is a thunderstorm-likelihood proxy:
a heavy rain hour landing on a high-CAPE day (say >~500 J/kg) is strong evidence
the downpour was **convective** -- a storm cell -- rather than gentle frontal
rain. That is exactly the localised-thunderstorm signal we are chasing, and it is
free from the same request. (An explicit modelled ``lightning_potential`` index
exists in Open-Meteo's high-res Historical Forecast API but not in this ERA5
archive endpoint, so it is intentionally not fetched here.)

Two outputs:

- ``docs/data/rainfall_intensity_by_site.csv`` -- tidy long form
  (date, site, lat, lon, rain_total_mm, rain_max_mm_per_h, peak_hour);
- ``docs/data/rainfall_intensity_daily_max.csv`` -- wide: one row per day, one
  column per site of the peak hourly intensity, plus ``catchment_max`` /
  ``catchment_max_site`` so you can see, per day, the worst downpour *anywhere*
  in the catchment and where it hit.

Like the other fetch scripts, the network step is separate because ERA5 needs
outbound access to ``archive-api.open-meteo.com``:

    python scripts/rainfall_intensity.py fetch     # -> the two CSVs above
    python scripts/rainfall_intensity.py sites      # just list the sites, no network

``fetch`` covers the same date range as the E. coli sampling programme by
default (min sample date minus a buffer .. max sample date); override with
``--start`` / ``--end``. Caveats: ERA5-Land (precipitation) is a ~9-11 km
reanalysis grid and ERA5 (CAPE) is coarser at ~25 km -- neither is a rain gauge,
and both *smooth* the sharpest convective peaks, so treat the intensity as a
lower bound and both fields as a relative (site-to-site, day-to-day) signal
rather than absolute readings. At ~25 km, CAPE is also barely site-specific
across this catchment; read it as a regional "was the airmass unstable?" flag.

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


def _request_hourly(params: dict) -> dict:
    url = ARCHIVE_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as response:
        data = json.load(response)
    if data.get("error"):
        raise RuntimeError(json.dumps(data, indent=2))
    return data.get("hourly", {})


def fetch_hourly(lat: float, lon: float, start: date, end: date) -> tuple[list[tuple[str, float, float]], int]:
    """Return ([(iso_hour, precipitation_mm, cape_j_per_kg), ...], n_cape_present).

    ``cape`` (Convective Available Potential Energy, J/kg) is the atmosphere's
    instability "fuel": high CAPE means a thunderstorm-favourable atmosphere. A
    heavy rain hour landing on a high-CAPE day is strong evidence the downpour
    was convective (a storm cell) rather than frontal drizzle.

    Two requests, on purpose. Precipitation is taken from the archive's default
    high-resolution model (ERA5-Land seamless, ~11 km) because localised
    convective cells are the whole point. But **ERA5-Land is a land-surface
    dataset and carries no CAPE** -- an unpinned ``hourly=cape`` request comes
    back all-null (silently written as 0.0, the bug this fixes). CAPE only exists
    in the full ERA5 atmospheric reanalysis (~25 km), so it is fetched in a
    second request pinned to ``models=era5`` and merged back by timestamp.

    ``n_cape_present`` counts how many hours actually returned a (non-null) CAPE
    value, so the caller can warn loudly if the field is empty again rather than
    silently writing zeros.
    """
    base = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "Europe/London",
    }
    precip_h = _request_hourly({**base, "hourly": "precipitation"})
    # CAPE from ERA5 (not ERA5-Land, which lacks it). Same range/timezone, so the
    # hour keys line up 1:1; merge by timestamp to be safe rather than by index.
    cape_h = _request_hourly({**base, "hourly": "cape", "models": "era5"})

    times = precip_h.get("time", [])
    precip = precip_h.get("precipitation", [None] * len(times))
    cape_by_hour = dict(zip(cape_h.get("time", []), cape_h.get("cape", [])))

    rows: list[tuple[str, float, float]] = []
    n_cape = 0
    for t, p in zip(times, precip):
        c = cape_by_hour.get(t)
        if c is not None:
            n_cape += 1
        rows.append((t, (p if p is not None else 0.0), (c if c is not None else 0.0)))
    return rows, n_cape


# Per-day tuple: (total_mm, peak_mm_per_h, peak_hour, cape_max, cape_at_peak_hour).
DayStats = tuple[float, float, int, float, float]


def daily_intensity(hourly: list[tuple[str, float, float]]) -> dict[str, DayStats]:
    """Collapse hourly rain + CAPE to per-day stats.

    Returns, per day: daily total rain, the heaviest single hour (peak
    intensity) and the hour it fell in, the day's max CAPE, and the CAPE during
    that heaviest rain hour (ties instability to the actual downpour).
    """
    by_day: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for iso_hour, mm, cape in hourly:
        day, _, clock = iso_hour.partition("T")
        hour = int(clock[:2]) if clock else 0
        by_day[day].append((hour, mm, cape))
    out: dict[str, DayStats] = {}
    for day, hours in by_day.items():
        total = sum(mm for _, mm, _ in hours)
        peak_hour, peak_mm, cape_at_peak = max(hours, key=lambda hmc: hmc[1])
        cape_max = max(c for _, _, c in hours)
        out[day] = (round(total, 2), round(peak_mm, 2), peak_hour,
                    round(cape_max, 1), round(cape_at_peak, 1))
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
    cape_present_total = 0
    for i, (name, lat, lon) in enumerate(SITES, 1):
        try:
            hourly, n_cape = fetch_hourly(lat, lon, start, end)
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"Could not reach Open-Meteo ({ARCHIVE_URL}): {exc}.\n"
                "Run `fetch` where archive-api.open-meteo.com egress is allowed, then commit\n"
                f"  {LONG_CSV}\n  {WIDE_CSV}"
            )
        cape_present_total += n_cape
        per_site[name] = daily_intensity(hourly)
        cape = "no CAPE!" if n_cape == 0 else f"CAPE ok ({n_cape}h)"
        print(f"  [{i:>2}/{len(SITES)}] {name}: {len(per_site[name])} days, {cape}")
        if i < len(SITES):
            time.sleep(0.5)  # be polite to the free API

    all_days = sorted({d for days in per_site.values() for d in days})

    if cape_present_total == 0:
        print("\n  WARNING: CAPE came back empty for every site. The ERA5 CAPE request\n"
              "  (models=era5) returned no values -- do NOT trust the cape_* columns.\n")

    # Long / tidy form.
    long_path = Path(args.long)
    long_path.parent.mkdir(parents=True, exist_ok=True)
    coords = {name: (lat, lon) for name, lat, lon in SITES}
    with long_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "site", "lat", "lon", "rain_total_mm", "rain_max_mm_per_h",
                         "peak_hour", "cape_max_j_per_kg", "cape_at_peak_hour_j_per_kg"])
        for name, _, _ in SITES:
            lat, lon = coords[name]
            for day in all_days:
                if day in per_site[name]:
                    total, peak_mm, peak_hour, cape_max, cape_at_peak = per_site[name][day]
                    writer.writerow([day, name, lat, lon, total, peak_mm, peak_hour, cape_max, cape_at_peak])

    # Wide form: peak hourly intensity per site per day + catchment-wide worst,
    # plus a catchment-wide CAPE summary so a day's convective potential sits
    # next to its heaviest downpour.
    site_names = [name for name, _, _ in SITES]
    wide_path = Path(args.wide)
    with wide_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date"] + site_names + [
            "catchment_max_mm_per_h", "catchment_max_site",
            "catchment_max_cape_j_per_kg", "catchment_max_cape_site"])
        for day in all_days:
            row = [day]
            best_mm, best_site = -1.0, ""
            best_cape, best_cape_site = -1.0, ""
            for name in site_names:
                if day in per_site[name]:
                    peak_mm = per_site[name][day][1]
                    cape_max = per_site[name][day][3]
                    row.append(peak_mm)
                    if peak_mm > best_mm:
                        best_mm, best_site = peak_mm, name
                    if cape_max > best_cape:
                        best_cape, best_cape_site = cape_max, name
                else:
                    row.append("")
            row.extend([
                round(best_mm, 2) if best_mm >= 0 else "", best_site,
                round(best_cape, 1) if best_cape >= 0 else "", best_cape_site])
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
                total, peak_mm, peak_hour, cape_max, cape_at_peak = per_site[name][day]
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
