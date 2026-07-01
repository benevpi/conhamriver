#!/usr/bin/env python3
"""
Download FreshWater Watch nitrate/phosphate data for Bristol Avon in 2025.

Usage:
    pip install requests
    python fww_bristol_avon_2025.py

Useful options:
    python fww_bristol_avon_2025.py --output bristol_avon_2025.csv
    python fww_bristol_avon_2025.py --require-text-match
    python fww_bristol_avon_2025.py --year 2025
"""

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


ITEMS = {
    "nitrate": "6dd54f74d8974206ab236394139e637d",
    "phosphate": "efb27c4d6c524d4aa79bbc5ae5e85d51",
}

ARCGIS_ITEM_URL = "https://www.arcgis.com/sharing/rest/content/items/{item_id}"
BRISTOL_AVON_BBOX = {
    # Approximate, deliberately broad:
    # west, south, east, north
    "xmin": -3.10,
    "ymin": 50.95,
    "xmax": -1.55,
    "ymax": 51.95,
}

DEFAULT_MATCH_TERMS = [
    "bristol avon",
    "bristol-avon",
    "bristolavon",
    "bristol avon riverblitz",
    "riverblitz",
    "bart",
]


def get_json(url: str, params: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error from {url}: {data['error']}")
    return data


def item_to_layer_urls(item_id: str) -> List[str]:
    """Resolve an ArcGIS Online item id into one or more queryable layer URLs."""
    item = get_json(ARCGIS_ITEM_URL.format(item_id=item_id), {"f": "json"})

    urls: List[str] = []

    if item.get("url"):
        urls.append(item["url"].rstrip("/"))

    # Some ArcGIS items store operational layers in the item data.
    data_url = ARCGIS_ITEM_URL.format(item_id=item_id) + "/data"
    try:
        item_data = get_json(data_url, {"f": "json"})
        for layer in item_data.get("operationalLayers", []):
            if layer.get("url"):
                urls.append(layer["url"].rstrip("/"))
    except Exception:
        pass

    # Deduplicate while preserving order.
    urls = list(OrderedDict.fromkeys(urls))

    queryable = []
    for url in urls:
        meta = get_json(url, {"f": "json"})

        # URL already points at a Feature Layer, e.g. .../FeatureServer/0
        if meta.get("type") == "Feature Layer" or "fields" in meta:
            queryable.append(url)
            continue

        # URL points at a Feature Service, e.g. .../FeatureServer
        for layer in meta.get("layers", []):
            layer_id = layer.get("id")
            if layer_id is not None:
                queryable.append(f"{url}/{layer_id}")

    if not queryable:
        raise RuntimeError(f"Could not find a queryable layer URL for item {item_id}")

    return list(OrderedDict.fromkeys(queryable))


def get_layer_metadata(layer_url: str) -> Dict[str, Any]:
    return get_json(layer_url, {"f": "json"})


def query_layer(layer_url: str) -> List[Dict[str, Any]]:
    """
    Query all features from a layer, using resultOffset pagination where possible.
    Falls back to objectId chunking if needed.
    """
    meta = get_layer_metadata(layer_url)
    max_count = int(meta.get("maxRecordCount") or 2000)

    features: List[Dict[str, Any]] = []
    offset = 0

    while True:
        params = {
            "f": "json",
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": max_count,
        }
        page = get_json(f"{layer_url}/query", params)
        batch = page.get("features", [])
        features.extend(batch)

        if not page.get("exceededTransferLimit") and len(batch) < max_count:
            break

        if not batch:
            break

        offset += len(batch)

    return features


def looks_like_epoch_ms(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 1_000_000_000_000


def parse_date(value: Any) -> Optional[dt.datetime]:
    if value is None or value == "":
        return None

    if looks_like_epoch_ms(value):
        return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)

    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None

        # ArcGIS / ISO-ish formats
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
        ):
            try:
                parsed = dt.datetime.strptime(s, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                return parsed
            except ValueError:
                pass

    return None


def find_date_fields(attributes: Dict[str, Any], fields_meta: List[Dict[str, Any]]) -> List[str]:
    date_fields = []

    for f in fields_meta:
        name = f.get("name")
        typ = f.get("type", "").lower()
        if name and "date" in typ:
            date_fields.append(name)

    for name in attributes:
        lname = name.lower()
        if any(token in lname for token in ["date", "sample", "survey", "created", "timestamp", "time"]):
            if name not in date_fields:
                date_fields.append(name)

    return date_fields


def record_year(attributes: Dict[str, Any], fields_meta: List[Dict[str, Any]]) -> Optional[int]:
    for field in find_date_fields(attributes, fields_meta):
        parsed = parse_date(attributes.get(field))
        if parsed:
            return parsed.year
    return None


def best_date_string(attributes: Dict[str, Any], fields_meta: List[Dict[str, Any]]) -> str:
    for field in find_date_fields(attributes, fields_meta):
        parsed = parse_date(attributes.get(field))
        if parsed:
            return parsed.date().isoformat()
    return ""


def extract_lon_lat(feature: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    geom = feature.get("geometry") or {}
    x = geom.get("x")
    y = geom.get("y")

    if x is None or y is None:
        return None, None

    try:
        lon = float(x)
        lat = float(y)
    except (TypeError, ValueError):
        return None, None

    # If coordinates are Web Mercator, convert to lon/lat.
    if abs(lon) > 180 or abs(lat) > 90:
        lon, lat = web_mercator_to_lonlat(lon, lat)

    return lon, lat


def web_mercator_to_lonlat(x: float, y: float) -> Tuple[float, float]:
    radius = 6378137.0
    lon = (x / radius) * 180.0 / math.pi
    lat = (2 * math.atan(math.exp(y / radius)) - math.pi / 2) * 180.0 / math.pi
    return lon, lat


def in_bbox(lon: Optional[float], lat: Optional[float], bbox: Dict[str, float]) -> bool:
    if lon is None or lat is None:
        return False
    return bbox["xmin"] <= lon <= bbox["xmax"] and bbox["ymin"] <= lat <= bbox["ymax"]


def text_blob(attributes: Dict[str, Any]) -> str:
    parts = []
    for value in attributes.values():
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (int, float, bool)):
            parts.append(str(value))
    return " ".join(parts).lower()


def matches_bristol_avon_text(attributes: Dict[str, Any], terms: Iterable[str]) -> bool:
    blob = text_blob(attributes)
    return any(term.lower() in blob for term in terms)


def find_value(attributes: Dict[str, Any], patterns: List[str]) -> Any:
    """
    Try to locate the main nitrate/phosphate value field.
    This keeps the script tolerant of field-name changes.
    """
    candidates = []
    for key, value in attributes.items():
        lkey = key.lower()
        if any(p in lkey for p in patterns):
            candidates.append((key, value))

    # Prefer numeric-looking values.
    for key, value in candidates:
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            s = value.strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", s):
                return s

    return candidates[0][1] if candidates else ""


def clean_record(
    feature: Dict[str, Any],
    source: str,
    fields_meta: List[Dict[str, Any]],
    match_terms: List[str],
    bbox: Dict[str, float],
) -> Dict[str, Any]:
    attrs = dict(feature.get("attributes") or {})
    lon, lat = extract_lon_lat(feature)

    text_match = matches_bristol_avon_text(attrs, match_terms)
    bbox_match = in_bbox(lon, lat, bbox)

    nitrate_value = find_value(attrs, ["nitrate", "no3", "nitrogen"])
    phosphate_value = find_value(attrs, ["phosphate", "po4", "phosphorus"])

    row: Dict[str, Any] = OrderedDict()
    row["source_layer"] = source
    row["sample_date"] = best_date_string(attrs, fields_meta)
    row["year"] = record_year(attrs, fields_meta) or ""
    row["lon"] = lon if lon is not None else ""
    row["lat"] = lat if lat is not None else ""
    row["bristol_avon_text_match"] = text_match
    row["bristol_avon_bbox_match"] = bbox_match
    row["nitrate_value_detected"] = nitrate_value
    row["phosphate_value_detected"] = phosphate_value

    # Preserve all raw ArcGIS attributes as well.
    for key, value in attrs.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        row[key] = value

    return row


def unique_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    """
    Used to merge duplicates across the nitrate/phosphate map layers.
    Prefer stable IDs if present; otherwise use date + rounded coords.
    """
    for key in ["GlobalID", "globalid", "GUID", "guid", "OBJECTID", "objectid"]:
        if row.get(key):
            return ("id", key, row[key])

    lon = row.get("lon")
    lat = row.get("lat")
    try:
        lon_r = round(float(lon), 6)
        lat_r = round(float(lat), 6)
    except Exception:
        lon_r = lon
        lat_r = lat

    return ("fallback", row.get("sample_date"), lon_r, lat_r)


def merge_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, ...], Dict[str, Any]] = OrderedDict()

    for row in rows:
        key = unique_key(row)
        if key not in merged:
            merged[key] = row
            continue

        existing = merged[key]

        # Combine layer labels.
        layers = set(str(existing.get("source_layer", "")).split(";"))
        layers.add(str(row.get("source_layer", "")))
        existing["source_layer"] = ";".join(sorted(x for x in layers if x))

        # Fill blanks and keep non-blank values.
        for k, v in row.items():
            if k not in existing or existing[k] in ("", None):
                existing[k] = v

    return list(merged.values())


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("No rows to write.")

    # Union all columns, preserving first-seen order.
    columns = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                columns.append(key)
                seen.add(key)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output", default="freshwaterwatch_bristol_avon_2025.csv")
    parser.add_argument(
        "--require-text-match",
        action="store_true",
        help="Only keep rows whose attributes mention Bristol Avon/RiverBlitz/BART. "
             "Without this, rows in the approximate Bristol Avon bbox are also kept.",
    )
    parser.add_argument(
        "--match-term",
        action="append",
        default=[],
        help="Additional text term to match in attributes. Can be repeated.",
    )
    args = parser.parse_args()

    match_terms = DEFAULT_MATCH_TERMS + args.match_term

    all_rows: List[Dict[str, Any]] = []

    for source, item_id in ITEMS.items():
        print(f"Resolving {source} ArcGIS item {item_id}...", file=sys.stderr)
        layer_urls = item_to_layer_urls(item_id)

        for layer_url in layer_urls:
            print(f"Downloading {source} layer: {layer_url}", file=sys.stderr)
            meta = get_layer_metadata(layer_url)
            fields_meta = meta.get("fields", [])

            features = query_layer(layer_url)
            print(f"  got {len(features)} features", file=sys.stderr)

            for feature in features:
                attrs = feature.get("attributes") or {}
                yr = record_year(attrs, fields_meta)
                if yr != args.year:
                    continue

                row = clean_record(
                    feature=feature,
                    source=source,
                    fields_meta=fields_meta,
                    match_terms=match_terms,
                    bbox=BRISTOL_AVON_BBOX,
                )

                if args.require_text_match:
                    keep = bool(row["bristol_avon_text_match"])
                else:
                    keep = bool(row["bristol_avon_text_match"] or row["bristol_avon_bbox_match"])

                if keep:
                    all_rows.append(row)

    rows = merge_rows(all_rows)

    if not rows:
        print(
            "No rows matched. Try without --require-text-match, or add a term, for example:\n"
            "  --match-term 'Bristol Avon Rivers Trust'\n"
            "You may also need to inspect the raw layer fields if Earthwatch has changed names.",
            file=sys.stderr,
        )
        return 1

    write_csv(args.output, rows)

    text_matches = sum(bool(r.get("bristol_avon_text_match")) for r in rows)
    bbox_matches = sum(bool(r.get("bristol_avon_bbox_match")) for r in rows)

    print(f"Wrote {len(rows)} rows to {args.output}")
    print(f"Rows with Bristol Avon text match: {text_matches}")
    print(f"Rows inside approximate Bristol Avon bbox: {bbox_matches}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())