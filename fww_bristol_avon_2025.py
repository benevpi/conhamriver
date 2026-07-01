#!/usr/bin/env python3

import argparse
import csv
import datetime as dt
import io
import json
import math
import re
import sys
from collections import OrderedDict
from urllib.parse import urlparse

import requests


START_PAGES = [
    "https://www.freshwaterwatch.org/pages/great-uk-waterblitz-results",
    "https://fww-earthw.hub.arcgis.com/pages/great-uk-waterblitz-results",
    "https://fww-earthw.hub.arcgis.com/pages/explore-our-data",
]

# Useful public IDs that are sometimes linked from FreshWater Watch / WaterBlitz pages.
# They are only seeds; the script still discovers and verifies the actual layers.
SEED_ITEM_IDS = [
    "cdda388f74744f49baaa5c0bc5f90973",  # Great UK WaterBlitz Survey123 form, visible in public search
    "ce12e88e956a43ea80b97b9e2b0663ad",  # older WaterBlitz SummaryViewer app seen in public links
]

ARCGIS_PORTALS = [
    "https://www.arcgis.com",
    "https://earthw.maps.arcgis.com",
    "https://surie.maps.arcgis.com",
]

ARCGIS_SEARCH_QUERIES = [
    '"Great UK WaterBlitz"',
    '"Great UK WaterBlitz Results"',
    '"FreshWater Watch" WaterBlitz',
    '"FreshWater Watch" nitrate phosphate',
    'WaterBlitz nitrate phosphate',
]

# Broad Bristol Avon / North Somerset Streams catchment-ish bbox.
# west, south, east, north
DEFAULT_BBOX = (-3.10, 50.95, -1.55, 51.95)

TEXT_MATCH_TERMS = [
    "bristol avon",
    "bristol-avon",
    "bristolavon",
    "bristol avon and north somerset",
    "bristol avon rivers trust",
    "bart",
    "riverblitz",
]

NITRATE_TERMS = ["nitrate", "nitrates", "no3", "nitrogen"]
PHOSPHATE_TERMS = ["phosphate", "phosphates", "po4", "phosphorus"]
DATE_TERMS = ["date", "sample", "survey", "created", "creation", "timestamp", "time"]


def eprint(*parts):
    sys.stderr.write(" ".join(str(p) for p in parts) + "\n")


def get_text(url, timeout=40):
    r = requests.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def get_json(url, params=None, timeout=60):
    if params is None:
        params = {}
    params = dict(params)
    params.setdefault("f", "json")

    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()

    try:
        data = r.json()
    except Exception:
        raise RuntimeError("Not JSON: {} {}".format(r.status_code, r.url))

    if isinstance(data, dict) and "error" in data:
        raise RuntimeError("ArcGIS error from {}: {}".format(r.url, data["error"]))

    return data


def post_json(url, data=None, timeout=90):
    if data is None:
        data = {}
    data = dict(data)
    data.setdefault("f", "json")

    r = requests.post(url, data=data, timeout=timeout)
    r.raise_for_status()

    try:
        out = r.json()
    except Exception:
        raise RuntimeError("Not JSON: {} {}".format(r.status_code, r.url))

    if isinstance(out, dict) and "error" in out:
        raise RuntimeError("ArcGIS error from {}: {}".format(r.url, out["error"]))

    return out


def find_item_ids_in_text(text):
    return set(re.findall(r"\b[a-f0-9]{32}\b", text, flags=re.I))


def find_service_urls_in_text(text):
    urls = set()

    # Capture ArcGIS REST service URLs embedded in HTML/JSON/script text.
    pattern = r"https?://[^\"'<>\\\s]+?/(?:FeatureServer|MapServer)(?:/\d+)?"
    for match in re.findall(pattern, text, flags=re.I):
        urls.add(match.rstrip("/"))

    return urls


def walk_json_for_ids_and_urls(obj, ids, urls):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()

            if isinstance(v, str):
                if re.fullmatch(r"[a-f0-9]{32}", v, flags=re.I):
                    ids.add(v)

                for found in find_item_ids_in_text(v):
                    ids.add(found)

                for found_url in find_service_urls_in_text(v):
                    urls.add(found_url)

            elif isinstance(v, (dict, list)):
                walk_json_for_ids_and_urls(v, ids, urls)

    elif isinstance(obj, list):
        for item in obj:
            walk_json_for_ids_and_urls(item, ids, urls)


def item_url(portal, item_id):
    return portal.rstrip("/") + "/sharing/rest/content/items/" + item_id


def fetch_item_any_portal(item_id):
    last_error = None

    for portal in ARCGIS_PORTALS:
        try:
            item = get_json(item_url(portal, item_id), {"f": "json"})
            if item.get("id"):
                return portal, item
        except Exception as exc:
            last_error = exc

    raise RuntimeError("Could not fetch item {}: {}".format(item_id, last_error))


def fetch_item_data(portal, item_id):
    url = item_url(portal, item_id) + "/data"

    try:
        r = requests.get(url, params={"f": "json"}, timeout=60)
        r.raise_for_status()
    except Exception as exc:
        eprint("  item data fetch failed:", item_id, exc)
        return None

    text = r.text.strip()

    if not text:
        return None

    try:
        return r.json()
    except Exception:
        return text


def arcgis_search():
    ids = set()

    for portal in ARCGIS_PORTALS:
        search_url = portal.rstrip("/") + "/sharing/rest/search"

        for query in ARCGIS_SEARCH_QUERIES:
            eprint("ArcGIS search:", portal, query)

            try:
                data = get_json(search_url, {
                    "q": query,
                    "num": 100,
                    "sortField": "modified",
                    "sortOrder": "desc",
                    "f": "json",
                })
            except Exception as exc:
                eprint("  search failed:", exc)
                continue

            for result in data.get("results", []):
                item_id = result.get("id")
                title = result.get("title", "")
                item_type = result.get("type", "")

                if item_id:
                    ids.add(item_id)
                    eprint("  found:", item_id, "|", item_type, "|", title)

    return ids


def discover():
    ids = set(SEED_ITEM_IDS)
    service_urls = set()

    # 1. Scrape the public pages for embedded item IDs and service URLs.
    for page in START_PAGES:
        eprint("Reading page:", page)
        try:
            html = get_text(page)
        except Exception as exc:
            eprint("  failed:", exc)
            continue

        ids.update(find_item_ids_in_text(html))
        service_urls.update(find_service_urls_in_text(html))

    # 2. Search ArcGIS for likely WaterBlitz/FreshWater Watch items.
    ids.update(arcgis_search())

    # 3. Recursively inspect item JSON/data for more IDs and service URLs.
    checked_ids = set()
    queue = list(ids)

    while queue and len(checked_ids) < 250:
        item_id = queue.pop(0)

        if item_id in checked_ids:
            continue

        checked_ids.add(item_id)

        try:
            portal, item = fetch_item_any_portal(item_id)
        except Exception:
            continue

        title = item.get("title", "")
        item_type = item.get("type", "")
        url = item.get("url")

        eprint("Inspecting item:", item_id, "|", item_type, "|", title)

        # Follow direct service URL on item if present.
        if isinstance(url, str):
            if "/FeatureServer" in url or "/MapServer" in url:
                service_urls.add(url.rstrip("/"))

        # Inspect item data/config.
        data = fetch_item_data(portal, item_id)
        new_ids = set()
        new_urls = set()

        if isinstance(data, (dict, list)):
            walk_json_for_ids_and_urls(data, new_ids, new_urls)
        elif isinstance(data, str):
            new_ids.update(find_item_ids_in_text(data))
            new_urls.update(find_service_urls_in_text(data))

        for new_id in new_ids:
            if new_id not in checked_ids:
                queue.append(new_id)

        service_urls.update(new_urls)

    return sorted(service_urls)


def bad_service_url(url):
    s = url.lower()

    # Known display/boundary services that caused your ewgis.org failure.
    if "fww_caba_boundaries" in s:
        return True

    if "boundar" in s and not any(x in s for x in ["waterblitz", "freshwater", "fww"]):
        return True

    return False


def get_layer_urls(service_url):
    service_url = service_url.rstrip("/")

    if bad_service_url(service_url):
        return []

    try:
        meta = get_json(service_url, {"f": "json"})
    except Exception as exc:
        eprint("Skipping unreachable service:", service_url, "|", exc)
        return []

    # Already a layer/table URL.
    if "fields" in meta:
        return [service_url]

    urls = []

    for group in ("layers", "tables"):
        for layer in meta.get(group, []):
            layer_id = layer.get("id")
            if layer_id is not None:
                urls.append(service_url + "/" + str(layer_id))

    return urls


def web_mercator_to_lonlat(x, y):
    radius = 6378137.0
    lon = (x / radius) * 180.0 / math.pi
    lat = (2 * math.atan(math.exp(y / radius)) - math.pi / 2) * 180.0 / math.pi
    return lon, lat


def parse_float(value):
    if value is None:
        return None
    try:
        s = str(value).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def parse_date(value):
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)) and value > 1_000_000_000_000:
        return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)

    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)

    s = str(value).strip()
    if not s:
        return None

    if re.fullmatch(r"\d{12,13}", s):
        return dt.datetime.fromtimestamp(int(s) / 1000, tz=dt.timezone.utc)

    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %B %Y",
    ]

    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(s, fmt)
            return parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass

    return None


def get_date_fields(row, fields_meta):
    fields = []

    for f in fields_meta:
        name = f.get("name")
        ftype = str(f.get("type", "")).lower()
        alias = str(f.get("alias", "")).lower()

        if not name:
            continue

        if "date" in ftype or any(t in name.lower() or t in alias for t in DATE_TERMS):
            fields.append(name)

    for key in row.keys():
        lk = str(key).lower()
        if any(t in lk for t in DATE_TERMS) and key not in fields:
            fields.append(key)

    return fields


def best_date(row, fields_meta):
    for key in get_date_fields(row, fields_meta):
        parsed = parse_date(row.get(key))
        if parsed:
            return parsed
    return None


def get_lon_lat(row, geom=None):
    if geom and "x" in geom and "y" in geom:
        lon = parse_float(geom.get("x"))
        lat = parse_float(geom.get("y"))

        if lon is not None and lat is not None:
            if abs(lon) > 180 or abs(lat) > 90:
                lon, lat = web_mercator_to_lonlat(lon, lat)
            return lon, lat

    key_map = {
        str(k).lower().replace(" ", "").replace("_", ""): k
        for k in row.keys()
    }

    lon = None
    lat = None

    for candidate in ["lon", "longitude", "long", "lng", "x", "xcoord"]:
        key = key_map.get(candidate)
        if key is not None:
            lon = parse_float(row.get(key))
            if lon is not None:
                break

    for candidate in ["lat", "latitude", "y", "ycoord"]:
        key = key_map.get(candidate)
        if key is not None:
            lat = parse_float(row.get(key))
            if lat is not None:
                break

    if lon is None or lat is None:
        return None, None

    if abs(lon) > 180 or abs(lat) > 90:
        lon, lat = web_mercator_to_lonlat(lon, lat)

    return lon, lat


def in_bbox(lon, lat, bbox):
    if lon is None or lat is None:
        return False
    west, south, east, north = bbox
    return west <= lon <= east and south <= lat <= north


def text_blob(row):
    return " ".join(str(v) for v in row.values() if v is not None).lower()


def text_match(row):
    blob = text_blob(row)
    return any(term in blob for term in TEXT_MATCH_TERMS)


def field_text(fields_meta):
    return " ".join(
        "{} {}".format(f.get("name", ""), f.get("alias", ""))
        for f in fields_meta
    ).lower()


def layer_score(layer_url, meta):
    fields = meta.get("fields", [])
    ft = field_text(fields)
    s = layer_url.lower() + " " + str(meta.get("name", "")).lower() + " " + ft

    score = 0

    if any(x in s for x in ["waterblitz", "freshwater", "freshwaterwatch", "fww"]):
        score += 4

    if any(x in s for x in NITRATE_TERMS):
        score += 4

    if any(x in s for x in PHOSPHATE_TERMS):
        score += 4

    if any(x in s for x in DATE_TERMS):
        score += 1

    if any(x in s for x in ["email", "address", "phone"]):
        score -= 2

    return score


def find_measure_value(row, terms):
    candidates = []

    for key, value in row.items():
        lk = str(key).lower()
        if any(term in lk for term in terms):
            candidates.append((key, value))

    # Prefer numeric/range-like values.
    for key, value in candidates:
        if value is None:
            continue
        s = str(value).strip()
        if not s:
            continue
        return s

    return ""


def query_layer(layer_url, bbox, max_pages=200):
    meta = get_json(layer_url, {"f": "json"})
    max_count = int(meta.get("maxRecordCount") or 1000)
    page_size = min(max_count, 2000)

    geometry = "{},{},{},{}".format(bbox[0], bbox[1], bbox[2], bbox[3])
    rows = []
    offset = 0

    while True:
        params = {
            "f": "json",
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }

        # Only use spatial filter for spatial layers. Tables will ignore it or error.
        if meta.get("geometryType"):
            params.update({
                "geometry": geometry,
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
            })

        page = post_json(layer_url.rstrip("/") + "/query", params)

        features = page.get("features", [])

        for feature in features:
            attrs = dict(feature.get("attributes") or {})
            geom = feature.get("geometry") or {}
            rows.append((attrs, geom))

        if not page.get("exceededTransferLimit") and len(features) < page_size:
            break

        if not features:
            break

        offset += len(features)

        if offset // page_size > max_pages:
            eprint("  stopping after max pages:", max_pages)
            break

    return rows, meta


def normalise_row(attrs, geom, meta, layer_url, year, bbox):
    d = best_date(attrs, meta.get("fields", []))

    if d is None or d.year != year:
        return None

    lon, lat = get_lon_lat(attrs, geom)

    # Keep rows if they either explicitly name Bristol Avon/BART/RiverBlitz,
    # or are within the Bristol Avon-ish bounding box.
    is_text_match = text_match(attrs)
    is_bbox_match = in_bbox(lon, lat, bbox)

    if not is_text_match and not is_bbox_match:
        return None

    out = OrderedDict()
    out["source_layer_url"] = layer_url
    out["sample_date"] = d.date().isoformat()
    out["year"] = d.year
    out["lon"] = lon if lon is not None else ""
    out["lat"] = lat if lat is not None else ""
    out["bristol_avon_text_match"] = is_text_match
    out["bristol_avon_bbox_match"] = is_bbox_match
    out["nitrate_value_detected"] = find_measure_value(attrs, NITRATE_TERMS)
    out["phosphate_value_detected"] = find_measure_value(attrs, PHOSPHATE_TERMS)

    for key, value in attrs.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        out[key] = value

    return out


def write_csv(path, rows):
    columns = []
    seen = set()

    for row in rows:
        for key in row.keys():
            if key not in seen:
                columns.append(key)
                seen.add(key)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_layer_audit(path, audits):
    columns = [
        "score",
        "queried",
        "row_count",
        "layer_url",
        "name",
        "geometryType",
        "fields",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(audits)


def dedupe_rows(rows):
    out = OrderedDict()

    for row in rows:
        key = (
            row.get("sample_date"),
            str(row.get("lon"))[:12],
            str(row.get("lat"))[:12],
            row.get("nitrate_value_detected"),
            row.get("phosphate_value_detected"),
        )
        out[key] = row

    return list(out.values())


def parse_bbox(s):
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    return tuple(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output", default="freshwaterwatch_bristol_avon_2025.csv")
    parser.add_argument("--layer-audit", default="fww_discovered_layers.csv")
    parser.add_argument(
        "--bbox",
        default=",".join(str(x) for x in DEFAULT_BBOX),
        help="west,south,east,north; default is a broad Bristol Avon bbox",
    )
    parser.add_argument(
        "--try-all-layers",
        action="store_true",
        help="Query all discovered layers, not just likely WaterBlitz/nutrient layers.",
    )
    args = parser.parse_args()

    bbox = parse_bbox(args.bbox)

    eprint("Discovering ArcGIS services...")
    services = discover()
    eprint("Discovered service URLs:", len(services))

    layer_urls = []
    for service in services:
        layer_urls.extend(get_layer_urls(service))

    layer_urls = sorted(set(layer_urls))
    eprint("Discovered layer/table URLs:", len(layer_urls))

    all_rows = []
    audits = []

    for layer_url in layer_urls:
        try:
            meta = get_json(layer_url, {"f": "json"})
        except Exception as exc:
            eprint("Could not inspect layer:", layer_url, exc)
            continue

        fields = meta.get("fields", [])
        score = layer_score(layer_url, meta)

        audit = {
            "score": score,
            "queried": False,
            "row_count": 0,
            "layer_url": layer_url,
            "name": meta.get("name", ""),
            "geometryType": meta.get("geometryType", ""),
            "fields": ";".join(f.get("name", "") for f in fields),
        }

        should_query = args.try_all_layers or score >= 5

        if not should_query:
            audits.append(audit)
            continue

        eprint("")
        eprint("Querying candidate layer, score", score)
        eprint(layer_url)

        try:
            raw_rows, meta = query_layer(layer_url, bbox)
        except Exception as exc:
            eprint("  query failed:", exc)
            audits.append(audit)
            continue

        audit["queried"] = True
        audit["row_count"] = len(raw_rows)

        eprint("  raw rows in bbox/table:", len(raw_rows))

        for attrs, geom in raw_rows:
            row = normalise_row(attrs, geom, meta, layer_url, args.year, bbox)
            if row is not None:
                all_rows.append(row)

        audits.append(audit)

    rows = dedupe_rows(all_rows)

    write_layer_audit(args.layer_audit, audits)

    if not rows:
        eprint("")
        eprint("No matching 2025 Bristol Avon rows found.")
        eprint("I wrote an audit file so you can see which ArcGIS layers were discovered:")
        eprint(" ", args.layer_audit)
        eprint("")
        eprint("Try this more aggressive run:")
        eprint("  python fww_bristol_avon_2025.py --try-all-layers")
        return 1

    write_csv(args.output, rows)

    print("Wrote {} rows to {}".format(len(rows), args.output))
    print("Layer audit written to {}".format(args.layer_audit))
    print("Rows with Bristol Avon text match:", sum(bool(r.get("bristol_avon_text_match")) for r in rows))
    print("Rows inside bbox:", sum(bool(r.get("bristol_avon_bbox_match")) for r in rows))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())