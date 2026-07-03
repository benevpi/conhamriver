#!/usr/bin/env python3
"""Rainfall *intensity* (max mm/hour within a day) across the Bristol Avon catchment.

Daily rainfall totals hide the thing that actually triggers a first-flush CSO
spill or a flashy runoff pulse: a short, violent burst. 15 mm falling over an
hour is a very different event from 15 mm drizzled over a day. Convective
thunderstorms are also *local* -- a cell can dump 20 mm on Keynsham while Conham
stays dry -- so a single point (as in `weather_conham_ecoli.py`) can miss them
entirely.

This script pulls **hourly** precipitation from the Open-Meteo ERA5 archive for
many sites spread across the Bristol Avon catchment (Bristol, Bath, the Chew and
Frome sub-catchments, and the upper Avon headwaters) and, for each site and day,
computes:

- ``rain_total_mm``      -- daily total (sanity check against the daily archive);
- ``rain_max_mm_per_h``  -- the heaviest single hour = peak intensity;
- ``peak_hour``          -- the local hour (0-23) that peak fell in.

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
``--start`` / ``--end``. Caveat: ERA5 is a ~9-11 km reanalysis grid, not a rain
gauge -- it will *smooth* the sharpest convective peaks, so treat the intensity
as a lower bound and a relative (site-to-site, day-to-day) signal rather than an
absolute gauge reading.

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


def fetch_hourly(lat: float, lon: float, start: date, end: date) -> list[tuple[str, float]]:
    """Return [(iso_hour, precipitation_mm), ...] for a site over [start, end]."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": "precipitation",
        "timezone": "Europe/London",
    }
    url = ARCHIVE_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as response:
        data = json.load(response)
    if data.get("error"):
        raise RuntimeError(json.dumps(data, indent=2))
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [None] * len(times))
    return [(t, (p if p is not None else 0.0)) for t, p in zip(times, precip)]


def daily_intensity(hourly: list[tuple[str, float]]) -> dict[str, tuple[float, float, int]]:
    """Collapse hourly rain to per-day (total, max_hourly, peak_hour)."""
    by_day: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for iso_hour, mm in hourly:
        day, _, clock = iso_hour.partition("T")
        hour = int(clock[:2]) if clock else 0
        by_day[day].append((hour, mm))
    out: dict[str, tuple[float, float, int]] = {}
    for day, hours in by_day.items():
        total = sum(mm for _, mm in hours)
        peak_hour, peak_mm = max(hours, key=lambda hm: hm[1])
        out[day] = (round(total, 2), round(peak_mm, 2), peak_hour)
    return out


def run_fetch(args) -> int:
    if args.start and args.end:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    else:
        dates = read_sample_dates(Path(args.samples))
        start = min(dates) - timedelta(days=BUFFER_DAYS)
        end = max(dates)
    print(f"Fetching hourly rainfall for {len(SITES)} sites, {start}..{end}")

    # site -> {day -> (total, max_hourly, peak_hour)}
    per_site: dict[str, dict[str, tuple[float, float, int]]] = {}
    for i, (name, lat, lon) in enumerate(SITES, 1):
        try:
            hourly = fetch_hourly(lat, lon, start, end)
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"Could not reach Open-Meteo ({ARCHIVE_URL}): {exc}.\n"
                "Run `fetch` where archive-api.open-meteo.com egress is allowed, then commit\n"
                f"  {LONG_CSV}\n  {WIDE_CSV}"
            )
        per_site[name] = daily_intensity(hourly)
        print(f"  [{i:>2}/{len(SITES)}] {name}: {len(per_site[name])} days")
        if i < len(SITES):
            time.sleep(0.5)  # be polite to the free API

    all_days = sorted({d for days in per_site.values() for d in days})

    # Long / tidy form.
    long_path = Path(args.long)
    long_path.parent.mkdir(parents=True, exist_ok=True)
    coords = {name: (lat, lon) for name, lat, lon in SITES}
    with long_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "site", "lat", "lon", "rain_total_mm", "rain_max_mm_per_h", "peak_hour"])
        for name, _, _ in SITES:
            lat, lon = coords[name]
            for day in all_days:
                if day in per_site[name]:
                    total, peak_mm, peak_hour = per_site[name][day]
                    writer.writerow([day, name, lat, lon, total, peak_mm, peak_hour])

    # Wide form: peak hourly intensity per site per day + catchment-wide worst.
    site_names = [name for name, _, _ in SITES]
    wide_path = Path(args.wide)
    with wide_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date"] + site_names + ["catchment_max_mm_per_h", "catchment_max_site"])
        for day in all_days:
            row = [day]
            best_mm, best_site = -1.0, ""
            for name in site_names:
                if day in per_site[name]:
                    peak_mm = per_site[name][day][1]
                    row.append(peak_mm)
                    if peak_mm > best_mm:
                        best_mm, best_site = peak_mm, name
                else:
                    row.append("")
            row.extend([round(best_mm, 2) if best_mm >= 0 else "", best_site])
            writer.writerow(row)

    print(f"Wrote {long_path} ({len(all_days) * len(SITES)} site-days)")
    print(f"Wrote {wide_path} ({len(all_days)} days x {len(SITES)} sites)")
    # Quick headline: the ten most intense downpours anywhere in the catchment.
    peaks = []
    for day in all_days:
        for name in site_names:
            if day in per_site[name]:
                peaks.append((per_site[name][day][1], day, name))
    peaks.sort(reverse=True)
    print("Ten most intense single hours in the catchment:")
    for mm, day, name in peaks[:10]:
        print(f"  {mm:6.1f} mm/h  {day}  {name}")
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
