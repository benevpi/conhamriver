#!/usr/bin/env python3
"""Extract the Bristol Avon rows from the FreshWater Watch Global Data Set.

The raw file (`Global_Data_Set_XvsX_0.csv`) is a ~73k-row global citizen-science
water-quality dataset. The management-catchment tag (`MNCAT_NAME`) is unreliable
for the Bristol Avon -- some genuinely local sites are mis-tagged (the tidal Avon
at Conham as "Severn England TraC", Kennet & Avon canal sites as "Avon
Hampshire"), and the catchment lumps in Somerset coastal streams that drain to
the Severn, not the Avon.

So this script selects rows by **site name + geography + water-body type**:

- geographically on the English side near Conham (within `--max-miles`, lon >= -2.75);
- a flowing watercourse (Freshwater body type River/Stream/Ditch, or a site name
  that reads like a river/brook/stream), excluding canals, locks, ponds, lakes,
  reservoirs, harbours and other still water;
- excluding a short list of North Somerset coastal streams (Land Yeo, Kenn, etc.)
  that flow to the Severn estuary rather than into the Bristol Avon.

It writes a tidy CSV with the useful measurement/observation columns plus derived
fields (watercourse group parsed from the site name, distance to Conham, and
whether the site is upstream of Conham). Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
from datetime import date
from pathlib import Path

CONHAM_LAT, CONHAM_LON = 51.444858, -2.534812

SOURCE_CSV = "Global_Data_Set_XvsX_0.csv"
OUTPUT_CSV = "docs/data/bristol_avon_freshwater_watch.csv"

# Still-water / artificial water bodies to drop (by site-name substring).
STILLWATER_WORDS = ("canal", "k&a", "k & a", "lock", "marina", "harbour", "dock",
                    "basin", "pond", "lake", "reservoir", "millpond", "mill pond")
# North Somerset coastal streams: in the management catchment but drain to the
# Severn estuary, not the Bristol Avon.
NON_AVON_WORDS = ("land yeo", "blind yeo", "congresbury yeo", "river kenn", "banwell", "nailsea")

# Watercourse grouping, first match wins (checked against the lowered site name).
WATERCOURSE_RULES = [
    ("River Chew", ("chew",)),
    ("River Frome", ("frome",)),
    ("River Boyd", ("boyd",)),
    ("River Trym", ("trym",)),
    ("Malago", ("malago",)),
    ("Brislington Brook", ("brislington",)),
    ("Siston Brook", ("siston",)),
    ("Warmley Brook", ("warmley",)),
    ("By Brook", ("by brook", "by brooke")),
    ("Cam Brook", ("cam brook",)),
    ("Wellow Brook", ("wellow",)),
    ("Midford Brook", ("midford",)),
    ("Semington Brook", ("semington",)),
    ("River Biss", ("biss",)),
    ("St Catherine's Brook", ("st catherine",)),
    ("River Avon", ("avon",)),
]

BODY_FLOWING = {"River", "Stream", "Ditch"}
RIVER_NAME_WORDS = ("river", "brook", "stream", "weir", " avon", "chew", "frome")


def haversine(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_date(s):
    try:
        d = (s or "").split()[0]
        mm, dd, yy = d.split("/")
        return date(int(yy), int(mm), int(dd))
    except Exception:
        return None


def value(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return ""


def watercourse_group(site_name):
    n = (site_name or "").lower()
    for label, words in WATERCOURSE_RULES:
        if any(w in n for w in words):
            return label
    # Fall back to a "<name> brook/stream" phrase parsed from the site name.
    m = re.search(r"([a-z'’.]+(?: [a-z'’.]+){0,2}) (brook|stream)", n)
    if m:
        return m.group(1).title() + " " + m.group(2).title()
    return "Other / unnamed"


def is_flowing(row):
    body = (row.get("Freshwater body type") or "").strip()
    name = (row.get("Site Name") or "").lower()
    if any(w in name for w in STILLWATER_WORDS):
        return False
    if body in BODY_FLOWING:
        return True
    if body in ("Pond", "Lake", "Canal", "Wetland"):
        return False
    # Body type blank/Other: fall back to the site name reading like a river.
    return any(w in name for w in RIVER_NAME_WORDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE_CSV)
    parser.add_argument("--output", default=OUTPUT_CSV)
    parser.add_argument("--max-miles", type=float, default=30.0, help="Max distance from Conham")
    args = parser.parse_args()

    src = Path(args.source)
    if not src.exists():
        raise SystemExit(f"{src} not found.")

    out_rows = []
    stats = {"scanned": 0, "in_area": 0, "dropped_stillwater": 0, "dropped_non_avon": 0}
    with src.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            stats["scanned"] += 1
            try:
                lat, lon = float(row["y"]), float(row["x"])
            except (TypeError, ValueError):
                continue
            dist = haversine(CONHAM_LAT, CONHAM_LON, lat, lon)
            if dist > args.max_miles or lon < -2.75:  # English side, near the Avon
                continue
            stats["in_area"] += 1
            name = (row.get("Site Name") or "")
            if any(w in name.lower() for w in NON_AVON_WORDS):
                stats["dropped_non_avon"] += 1
                continue
            if not is_flowing(row):
                stats["dropped_stillwater"] += 1
                continue
            dt = parse_date(row.get("Sample Date", ""))
            out_rows.append({
                "sample_date": dt.isoformat() if dt else "",
                "site_name": name.strip(),
                "watercourse": watercourse_group(name),
                "freshwater_body_type": row.get("Freshwater body type", ""),
                "latitude": lat,
                "longitude": lon,
                "distance_to_conham_mi": round(dist, 2),
                "upstream_of_conham": lon > CONHAM_LON,
                "group_name": row.get("Group Name", ""),
                "mncat_name": row.get("MNCAT_NAME", ""),
                "county": row.get("County", ""),
                "nitrate_mgL": value(row, "Nitrate (mg/L) MID", "Nitrate (mg/L)"),
                "phosphate_mgL": value(row, "Phosphate (mg/L) MID", "Phosphate (mg/L)"),
                "secchi_turbidity": row.get("Water quality - Secchi Tube (Turbidity)", ""),
                "rain_last_24h": row.get("Has there been any rain during the last 24 hours?", ""),
                "pollution_sources": row.get("Are there any pollution sources in the immediate surroundings? (select all that apply)", ""),
                "main_land_use": row.get("What is the main land use within 50m?", ""),
                "notes": (row.get("notes") or "").replace("\n", " ").strip(),
            })

    out_rows.sort(key=lambda r: (r["sample_date"], r["distance_to_conham_mi"]))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)

    from collections import Counter
    years = Counter(r["sample_date"][:4] for r in out_rows if r["sample_date"])
    wc = Counter(r["watercourse"] for r in out_rows)
    print(f"Wrote {out} ({len(out_rows)} river/stream rows)")
    print(f"  scanned {stats['scanned']}, in-area {stats['in_area']}, "
          f"dropped still-water {stats['dropped_stillwater']}, dropped non-Avon {stats['dropped_non_avon']}")
    print(f"  by year: {dict(sorted(years.items()))}")
    print("  top watercourses: " + ", ".join(f"{k} ({v})" for k, v in wc.most_common(8)))


if __name__ == "__main__":
    raise SystemExit(main())
