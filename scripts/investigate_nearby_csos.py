#!/usr/bin/env python3
"""Hunt for nearby CSOs -- on ANY watercourse -- that could explain the spikes.

The other Conham scripts only look at storm overflows whose ReceivingWatercourse
is one of seven hard-coded names (River Avon, River Chew, a few brooks). The
high-E. coli days the models could not explain (e.g. 2025-09-27 and 2025-11-22,
both 1000 CFU/100ml) had NO recorded spill in that filtered set -- so if a CSO
did spill, it must be on a watercourse the filter excludes.

This script casts a wider net: it queries the same Wessex Water Event Duration
Monitoring 2025 ArcGIS view by GEOGRAPHY (a bounding box around Conham and the
upstream Avon corridor) with no watercourse-name filter, then reports any
outfalls -- especially on watercourses NOT in the Conham list -- that spilled in
the run-up to each high-E. coli sample.

    python scripts/investigate_nearby_csos.py fetch    # ArcGIS -> nearby events CSV (needs network)
    python scripts/investigate_nearby_csos.py report   # offline: which spilled before the spikes

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
CONHAM_LAT = 51.444858
CONHAM_LON = -2.534812

# Watercourses already modelled (lower-cased). Anything outside this set is the
# blind spot we are looking for.
CONHAM_RIVERS = {
    "river avon", "river chew", "charlton bottom via sws", "bathford brook (s)",
    "horsecombe brook", "river avon via sws", "river avon (via sws)",
}

# Bounding box: Conham westward edge across to east of Bath, covering the
# upstream Avon corridor and its tributaries.
BBOX = {"min_lat": 51.30, "max_lat": 51.55, "min_lon": -2.62, "max_lon": -2.10}
MAX_DISTANCE_MILES = 15.0      # only report outfalls within this of Conham
LOOKBACK_DAYS = 7

NEARBY_EVENTS_CSV = "docs/data/conham_nearby_cso_events.csv"
SAMPLES_CSV = "docs/data/conham_sampling_2025_2026_e_coli.csv"
REPORT_MD = "docs/data/conham_nearby_cso_investigation.md"
HIGH_THRESHOLD = 450.0


def haversine(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def ms_to_dt(value):
    if value in (None, 0):
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def read_samples(path: Path) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {r["sample_date"]: float(r["cfu_per_100ml"]) for r in csv.DictReader(handle)}


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def fetch_events(start: datetime, end: datetime, page_size: int, sleep_s: float) -> list[dict]:
    where = (
        f"OutfallLatitude >= {BBOX['min_lat']} AND OutfallLatitude <= {BBOX['max_lat']} AND "
        f"OutfallLongitude >= {BBOX['min_lon']} AND OutfallLongitude <= {BBOX['max_lon']} AND "
        f"EventStart >= DATE '{start:%Y-%m-%d %H:%M:%S}' AND EventStart < DATE '{end:%Y-%m-%d %H:%M:%S}'"
    )
    features, offset = [], 0
    while True:
        params = {
            "where": where,
            "outFields": "SiteId,SiteName,ReceivingWatercourse,EventStart,EventEnd,Duration,OutfallLatitude,OutfallLongitude",
            "orderByFields": "EventStart ASC",
            "f": "json",
            "resultRecordCount": str(page_size),
            "resultOffset": str(offset),
            "returnExceededLimitFeatures": "true",
        }
        url = ARCGIS_QUERY_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.load(resp)
        if "error" in data:
            raise RuntimeError(json.dumps(data["error"], indent=2))
        page = data.get("features", [])
        features.extend(page)
        if len(page) < page_size or not data.get("exceededTransferLimit"):
            break
        offset += page_size
        time.sleep(sleep_s)
    return features


def run_fetch(args) -> int:
    samples = read_samples(Path(args.samples))
    sample_dates = sorted(date.fromisoformat(d) for d in samples)
    start = datetime.combine(min(sample_dates) - timedelta(days=LOOKBACK_DAYS + 1), dt_time.min, tzinfo=timezone.utc)
    end = datetime.combine(max(sample_dates) + timedelta(days=1), dt_time.min, tzinfo=timezone.utc)
    try:
        feats = fetch_events(start, end, args.page_size, args.sleep)
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Could not reach the ArcGIS 2025 EDM view: {exc}.\n"
            "Run `fetch` where services.arcgis.com egress is allowed, commit "
            f"{args.events}, then run the `report` step."
        )
    rows = []
    for f in feats:
        a = f.get("attributes", {})
        lat, lon = a.get("OutfallLatitude"), a.get("OutfallLongitude")
        if lat is None or lon is None:
            continue
        wc = (a.get("ReceivingWatercourse") or "").strip()
        rows.append({
            "site_id": a.get("SiteId"),
            "site_name": a.get("SiteName"),
            "receiving_watercourse": wc,
            "in_conham_filter": wc.lower() in CONHAM_RIVERS,
            "outfall_lat": lat,
            "outfall_lon": lon,
            "distance_miles": round(haversine(CONHAM_LAT, CONHAM_LON, float(lat), float(lon)), 3),
            "upstream": float(lon) > CONHAM_LON,
            "event_start": (ms_to_dt(a.get("EventStart")) or "").isoformat() if ms_to_dt(a.get("EventStart")) else "",
            "event_end": (ms_to_dt(a.get("EventEnd")) or "").isoformat() if ms_to_dt(a.get("EventEnd")) else "",
            "duration_hours": round((a.get("Duration") or 0) / 60, 2) if a.get("Duration") else "",
        })
    out = Path(args.events)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else
                                ["site_id", "site_name", "receiving_watercourse", "in_conham_filter",
                                 "outfall_lat", "outfall_lon", "distance_miles", "upstream",
                                 "event_start", "event_end", "duration_hours"])
        writer.writeheader()
        writer.writerows(rows)
    others = sorted({r["receiving_watercourse"] for r in rows if not r["in_conham_filter"]})
    print(f"Wrote {out} ({len(rows)} events from {len(feats)} features)")
    print(f"Watercourses NOT in the Conham filter that appear nearby: {len(others)}")
    for w in others:
        print(f"  - {w}")
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def load_events(path: Path) -> list[dict]:
    out = []
    with path.open(newline="", encoding="utf-8") as handle:
        for r in csv.DictReader(handle):
            if not r.get("event_start"):
                continue
            r["distance_miles"] = float(r["distance_miles"]) if r["distance_miles"] else 999.0
            r["in_conham_filter"] = str(r["in_conham_filter"]).lower() == "true"
            r["upstream"] = str(r["upstream"]).lower() == "true"
            r["_start"] = datetime.fromisoformat(r["event_start"])
            out.append(r)
    return out


def run_report(args) -> int:
    events_path = Path(args.events)
    if not events_path.exists():
        raise SystemExit(
            f"{events_path} not found. Run `python {Path(__file__).name} fetch` first "
            "(needs network access to services.arcgis.com)."
        )
    ecoli = read_samples(Path(args.samples))
    events = load_events(events_path)
    high_days = [d for d in sorted(ecoli) if ecoli[d] >= HIGH_THRESHOLD]

    lines = [
        "# Nearby CSOs that could explain the unexplained Conham spikes",
        "",
        "Generated by `scripts/investigate_nearby_csos.py`. Storm-overflow events within",
        f"a bounding box around Conham (any watercourse, <= {MAX_DISTANCE_MILES:g} miles, upstream)",
        "in the 7 days before each high-E. coli sample. Outfalls on watercourses **not**",
        "in the existing Conham filter are the candidates of interest -- they were invisible",
        "to every earlier model.",
        "",
        "## Watercourses near Conham not covered by the existing models",
        "",
    ]
    other_wcs = sorted({e["receiving_watercourse"] for e in events
                        if not e["in_conham_filter"] and e["distance_miles"] <= MAX_DISTANCE_MILES and e["upstream"]})
    if other_wcs:
        for w in other_wcs:
            lines.append(f"- {w}")
    else:
        lines.append("- (none found in range)")
    lines.append("")

    for d in high_days:
        end = datetime.combine(date.fromisoformat(d), dt_time.min, tzinfo=timezone.utc)
        start = end - timedelta(days=LOOKBACK_DAYS)
        window = [e for e in events
                  if start <= e["_start"] < end and e["distance_miles"] <= MAX_DISTANCE_MILES and e["upstream"]]
        window.sort(key=lambda e: e["distance_miles"])
        outside = [e for e in window if not e["in_conham_filter"]]
        lines.append(f"## {d} -- E. coli {ecoli[d]:.0f} CFU/100ml")
        lines.append("")
        lines.append(f"{len(window)} upstream spill events in the 7-day window; "
                     f"{len(outside)} on watercourses outside the Conham filter.")
        lines.append("")
        if window:
            lines.append("| Outfall | Watercourse | In filter? | Dist (mi) | Start | Dur (h) |")
            lines.append("|---|---|:---:|---:|---|---:|")
            for e in window[:25]:
                dur = e["duration_hours"]
                lines.append(
                    f"| {e['site_name']} | {e['receiving_watercourse']} | "
                    f"{'yes' if e['in_conham_filter'] else '**NO**'} | {e['distance_miles']:.1f} | "
                    f"{e['event_start'][:16]} | {dur if dur != '' else '?'} |"
                )
        else:
            lines.append("_No upstream spills recorded near Conham in this window -- on any watercourse._")
        lines.append("")

    lines.extend([
        "## How to read this",
        "",
        "- An outfall marked **NO** in the filter column spilled near Conham but was",
        "  excluded from every earlier model -- a candidate explanation for that day.",
        "- A day with *no* events at all (on any watercourse) cannot be explained by",
        "  monitored CSOs and points to an unmonitored source, diffuse/agricultural",
        "  runoff, or a data gap in the EDM feed.",
        "- Bounding box / distance are crude proxies for hydrological connectivity; a",
        "  spill being nearby does not prove it reached Conham.",
        "",
    ])
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.report}")
    print(f"High days examined: {len(high_days)}; other-watercourse outfalls nearby: {len(other_wcs)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    f = sub.add_parser("fetch", help="Query ArcGIS for nearby CSO events by geography (needs network)")
    f.add_argument("--samples", default=SAMPLES_CSV)
    f.add_argument("--events", default=NEARBY_EVENTS_CSV)
    f.add_argument("--page-size", type=int, default=2000)
    f.add_argument("--sleep", type=float, default=0.1)
    f.set_defaults(func=run_fetch)
    r = sub.add_parser("report", help="Report which nearby outfalls spilled before the spikes (offline)")
    r.add_argument("--samples", default=SAMPLES_CSV)
    r.add_argument("--events", default=NEARBY_EVENTS_CSV)
    r.add_argument("--report", default=REPORT_MD)
    r.set_defaults(func=run_report)
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
