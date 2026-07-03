#!/usr/bin/env python3
"""Daily CSO spill hours for Conham's upstream watercourses, all of 2025.

The E. coli feature table (`conham_cso_ecoli_features.csv`) only holds CSO totals
on the 25 sample dates. To plot CSO spilling *every day* we need a continuous
daily series. This script queries the same Wessex Water Event Duration Monitoring
2025 ArcGIS view and the same Conham watercourse filter as
`analyze_conham_cso_ecoli.py`, over the whole year in one pass, and aggregates
spill duration into a daily calendar:

- ``spill_hours_day``  -- hours of spilling from events that *started* that day
  (an event's full duration is counted on its start date, exactly as the sample
  features count it);
- ``spill_hours_2d``   -- trailing 2-day cumulative sum (that day + the day before);
- ``spill_hours_7d``   -- trailing 7-day cumulative sum (that day + the prior 6);
- ``event_count_day``  -- number of spill events starting that day.

The trailing windows end *on* each day (inclusive), which is the natural reading
for a daily chart ("as of this day, how much spilling in the last 2 / 7 days").
Note this differs by one day from the sample-marker windows in
`conham_cso_ecoli_features.csv`, which end at midnight *before* the sample and so
exclude the sample day itself.

Network step is separate, like the other fetch scripts, because it needs
outbound access to ``services.arcgis.com``:

    python scripts/daily_cso.py fetch     # ArcGIS -> conham_cso_daily.csv (+ raw events)
    python scripts/daily_cso.py build      # offline: re-aggregate daily CSV from the raw events

`fetch` writes both the raw per-event dump (`conham_cso_events_2025.csv`) and the
daily aggregate (`conham_cso_daily.csv`); commit both. `build` lets you
re-aggregate the daily CSV from the committed raw events without re-querying.

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
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path

ARCGIS_QUERY_URL = "https://services.arcgis.com/3SZ6e0uCvPROr4mS/arcgis/rest/services/Wessex_Water_Event_Duration_Monitoring_2025_view/FeatureServer/0/query"
# Same Conham upstream watercourses as analyze_conham_cso_ecoli.py / poo.py.
CONHAM_RIVERS = [
    "RIVER AVON",
    "RIVER CHEW",
    "charlton bottom via sws",
    "bathford brook (s)",
    "horsecombe brook",
    "river avon via sws",
    "river avon (via sws)",
]

EVENTS_CSV = "docs/data/conham_cso_events_2025.csv"
DAILY_CSV = "docs/data/conham_cso_daily.csv"
# Fetch the whole 2025 season; a little slack before the first sample so the
# trailing 7-day sums are complete from the start of the plotted range.
FETCH_START = date(2025, 1, 1)
FETCH_END = date(2026, 1, 1)  # exclusive upper bound


def ms_to_datetime(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def arcgis_where(start: datetime, end: datetime) -> str:
    river_clause = " OR ".join(f"ReceivingWatercourse = '{r}'" for r in CONHAM_RIVERS)
    s = start.strftime("%Y-%m-%d %H:%M:%S")
    e = end.strftime("%Y-%m-%d %H:%M:%S")
    return f"({river_clause}) AND EventStart >= DATE '{s}' AND EventStart < DATE '{e}'"


def fetch_page(params: dict) -> dict:
    url = ARCGIS_QUERY_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.load(response)
    if "error" in data:
        raise RuntimeError(json.dumps(data["error"], indent=2))
    return data


def fetch_events(start: datetime, end: datetime, page_size: int, sleep_seconds: float) -> list[dict]:
    """All (deduplicated) spill events on the Conham rivers in [start, end)."""
    seen: set[tuple] = set()
    events: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": arcgis_where(start, end),
            "outFields": "SiteId,SiteName,ReceivingWatercourse,EventId,EventStart,EventEnd",
            "orderByFields": "EventStart ASC",
            "f": "json",
            "resultRecordCount": str(page_size),
            "resultOffset": str(offset),
            "returnExceededLimitFeatures": "true",
        }
        data = fetch_page(params)
        page = data.get("features", [])
        for feature in page:
            a = feature.get("attributes", {})
            es, ee = ms_to_datetime(a.get("EventStart")), ms_to_datetime(a.get("EventEnd"))
            if es is None or ee is None:
                continue
            key = (a.get("EventId") or a.get("SiteId"), a.get("EventStart"), a.get("EventEnd"))
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "site_id": a.get("SiteId", ""),
                "site_name": a.get("SiteName", ""),
                "receiving_watercourse": a.get("ReceivingWatercourse", ""),
                "event_start": es.isoformat(),
                "event_end": ee.isoformat(),
                "duration_hours": round((ee - es).total_seconds() / 3600, 4),
            })
        if len(page) < page_size or not data.get("exceededTransferLimit"):
            break
        offset += page_size
        time.sleep(sleep_seconds)
    return events


def _write_events(events: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as h:
        writer = csv.DictWriter(h, fieldnames=[
            "site_id", "site_name", "receiving_watercourse",
            "event_start", "event_end", "duration_hours"])
        writer.writeheader()
        writer.writerows(events)


def aggregate_daily(events: list[dict]) -> list[dict]:
    """Daily spill hours + trailing 2-/7-day cumulative sums over the fetch range."""
    by_day_hours: dict[date, float] = defaultdict(float)
    by_day_events: dict[date, int] = defaultdict(int)
    for e in events:
        d = datetime.fromisoformat(e["event_start"]).date()
        by_day_hours[d] += float(e["duration_hours"])
        by_day_events[d] += 1

    rows = []
    d = FETCH_START
    while d < FETCH_END:
        def trailing(n):
            return round(sum(by_day_hours.get(d - timedelta(days=k), 0.0) for k in range(n)), 2)
        rows.append({
            "date": d.isoformat(),
            "spill_hours_day": round(by_day_hours.get(d, 0.0), 2),
            "spill_hours_2d": trailing(2),
            "spill_hours_7d": trailing(7),
            "event_count_day": by_day_events.get(d, 0),
        })
        d += timedelta(days=1)
    return rows


def _write_daily(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as h:
        writer = csv.DictWriter(h, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_fetch(args) -> int:
    start = datetime.combine(FETCH_START, dt_time.min, tzinfo=timezone.utc)
    end = datetime.combine(FETCH_END, dt_time.min, tzinfo=timezone.utc)
    try:
        events = fetch_events(start, end, args.page_size, args.sleep)
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Could not reach ArcGIS ({ARCGIS_QUERY_URL}): {exc}.\n"
            "Run `fetch` where services.arcgis.com egress is allowed, then commit\n"
            f"  {EVENTS_CSV}\n  {DAILY_CSV}"
        )
    _write_events(events, Path(args.events))
    rows = aggregate_daily(events)
    _write_daily(rows, Path(args.daily))
    spill_days = sum(1 for r in rows if r["spill_hours_day"] > 0)
    print(f"Wrote {args.events} ({len(events)} events)")
    print(f"Wrote {args.daily} ({len(rows)} days, {spill_days} with spilling)")
    return 0


def run_build(args) -> int:
    events_path = Path(args.events)
    if not events_path.exists():
        raise SystemExit(f"{events_path} not found. Run `fetch` first (needs ArcGIS access).")
    with events_path.open(newline="", encoding="utf-8") as h:
        events = list(csv.DictReader(h))
    rows = aggregate_daily(events)
    _write_daily(rows, Path(args.daily))
    print(f"Wrote {args.daily} ({len(rows)} days) from {len(events)} committed events")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    f = sub.add_parser("fetch", help="Query ArcGIS and write raw events + daily CSV (needs network)")
    f.add_argument("--events", default=EVENTS_CSV)
    f.add_argument("--daily", default=DAILY_CSV)
    f.add_argument("--page-size", type=int, default=2000)
    f.add_argument("--sleep", type=float, default=0.2)
    f.set_defaults(func=run_fetch)

    b = sub.add_parser("build", help="Re-aggregate the daily CSV from committed raw events (offline)")
    b.add_argument("--events", default=EVENTS_CSV)
    b.add_argument("--daily", default=DAILY_CSV)
    b.set_defaults(func=run_build)

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
