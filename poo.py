import requests
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2
import os
import json
from string import Template

index_data = []

def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def seconds_to_h_m(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return hours, minutes

# Fetch precipitation forecast from open-meteo and return warning messages
def get_precipitation_warnings(lat, lon):
    """Return a list of warning strings for days with >5mm rain in next 3 days."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "forecast_days": 3,
        "timezone": "UTC",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        # On failure, just return an empty list
        print(f"Failed to fetch weather data: {e}")
        return []

    warnings = []
    times = data.get("daily", {}).get("time", [])
    precip = data.get("daily", {}).get("precipitation_sum", [])
    for day, rain in zip(times, precip):
        if rain is not None and rain > 5:
            warnings.append(
                f"{day}: {rain}mm of rain forecast - water quality likely to get worse after this day"
            )
    return warnings

# Upstream filter functions for each site
def is_upstream_conham(lat, lon):
    # Upstream if longitude is greater (i.e., east of the swim site)
    return lon > -2.534812
    
def is_upstream_salford(lat, lon):
    # Upstream if longitude is greater (i.e., east of the swim site)
    return lon > -2.457616
    
def is_upstream_warleigh(lat, lon):
    # Upstream if longitude is greater (i.e., east of the swim site)
    return lat < 51.3736

def is_upstream_chew(lat, lon):
    # Upstream if west of -2.5432 (i.e., longitude less than -2.5432)
    return lon < -2.5432

# For Farleigh Hungerford on the River Frome, upstream CSOs are generally
# to the south of the swim spot. Treat discharges south of the site as
# upstream based on latitude.
def is_upstream_farleigh(lat, lon):
    # Upstream if latitude is less than the site's latitude
    return lat < 51.3299

def generate_report(river_name, river_label, rivers_to_query, ref_lat, ref_lon, filename, upstream_func):
    now = datetime.utcnow()
    two_days_ago_dt = now - timedelta(days=2)
    two_days_ago_str = two_days_ago_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Build where clause for the selected rivers
    conditions = " OR ".join([f"ReceivingWaterCourse = '{river}'" for river in rivers_to_query])
    where_clause = f"({conditions}) AND LatestEventStart >= DATE '{two_days_ago_str}'"

    url = "https://services.arcgis.com/3SZ6e0uCvPROr4mS/ArcGIS/rest/services/Wessex_Water_Storm_Overflow_Activity/FeatureServer/0/query"

    params = {
        "where": where_clause,
        "outFields": "Id,Company,Status,StatusStart,LatestEventStart,LatestEventEnd,Latitude,Longitude,ReceivingWaterCourse,LastUpdated",
        "orderByFields": "LatestEventStart DESC",
        "f": "json",
        "resultRecordCount": 1000
    }

    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    band_edges = [1, 5, 10, 20, 50]
    band_labels = [
        "Within 1 mile",
        "1 to 5 miles",
        "5 to 10 miles",
        "10 to 20 miles",
        "20 to 50 miles"
    ]
    band_durations = [0] * len(band_edges)

    if data.get("features"):
        for feat in data["features"]:
            attrs = feat["attributes"]
            start = attrs.get('LatestEventStart')
            end = attrs.get('LatestEventEnd')
            lat = attrs.get('Latitude')
            lon = attrs.get('Longitude')
            if start and end and lat is not None and lon is not None and upstream_func(lat, lon):
                duration_seconds = (end - start) / 1000
                dist = haversine(ref_lat, ref_lon, lat, lon)
                for i, edge in enumerate(band_edges):
                    lower = 0 if i == 0 else band_edges[i-1]
                    if lower < dist <= edge:
                        band_durations[i] += duration_seconds
                        break

    # Risk calculation as before
    total_seconds = sum(band_durations)
    risk = "Low"
    if band_durations[0] > 0 or sum(band_durations[0:2]) > 1800:
        risk = "High"
    elif total_seconds == 0:
        risk = "Low"
    else:
        risk = "Medium"
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    table_rows = ""
    for i, label in enumerate(band_labels):
        hours, minutes = seconds_to_h_m(band_durations[i])
        table_rows += f"<tr><td>{label}</td><td>{hours} hours {minutes} minutes</td></tr>\n"

    warnings = get_precipitation_warnings(ref_lat, ref_lon)
    weather_message = "<br>".join(warnings) if warnings else ""

    template_path = os.path.join("templates", "report_template.html")
    with open(template_path, "r", encoding="utf-8") as tpl_file:
        tpl = Template(tpl_file.read())

    html = tpl.substitute(
        river_label=river_label,
        risk=risk,
        risk_lower=risk.lower(),
        report_time=report_time,
        poo_emoji_block="<span class='poo-emoji'>💩</span>" if risk == "High" else "",
        table_rows=table_rows,
        weather_message=weather_message,
    )

    os.makedirs("docs", exist_ok=True)
    with open(f"docs/{filename}.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML report written to docs/{filename}.html")
    return risk, warnings

# --- Define rivers/reports you want to generate ---
reports = [
    {
        "river_name": "conham",
        "river_label": "Avon at Conham River",
        "rivers_to_query": [
            'RIVER AVON', 'RIVER CHEW', 'charlton bottom via sws', 'bathford brook (s)', 'horsecombe brook', 'river avon via sws'
        ],
        "ref_lat": 51.444858,
        "ref_lon": -2.534812,
        "filename": "conham",
        "upstream_func": is_upstream_conham,
    },
    {
        "river_name": "salford",
        "river_label": "Avon at Salford",
        "rivers_to_query": [
            'RIVER AVON', 'bathford brook (s)', 'horsecombe brook', 'river avon via sws'
        ],
        "ref_lat": 51.444858,
        "ref_lon": -2.534812,
        "filename": "salford",
        "upstream_func": is_upstream_salford,
    },
    {
        "river_name": "warleigh",
        "river_label": "Avon at Warleigh Weir",
        "rivers_to_query": [
            'RIVER AVON', 'bathford brook (s)','Bristol Avon', 'River Frome', 'river avon via sws'
        ],
        "ref_lat": 51.376556,
        "ref_lon":  -2.301611,
        "filename": "warleigh",
        "upstream_func": is_upstream_warleigh,
    },
    {
        "river_name": "chew",
        "river_label": "River Chew at Publow",
        "rivers_to_query": [
            'RIVER CHEW',
            'winford brook',
            'river chew(s)',
        ],
        "ref_lat": 51.375278, -2.543306
        "ref_lon": -2.543306,
        "filename": "chew",
        "upstream_func": is_upstream_chew,
    },
    {
        "river_name": "farleigh",
        "river_label": "River Frome at Farleigh Hungerford",
        "rivers_to_query": [
            'River Frome',
            'Bristol Avon',
            'river avon via sws'
        ],
        "ref_lat": 51.3299,
        "ref_lon": -2.288,
        "filename": "farleigh",
        "upstream_func": is_upstream_farleigh,
    },
    # Add more dicts here for other rivers/sites as needed
]

for r in reports:
    risk, warnings = generate_report(
        river_name=r["river_name"],
        river_label=r["river_label"],
        rivers_to_query=r["rivers_to_query"],
        ref_lat=r["ref_lat"],
        ref_lon=r["ref_lon"],
        filename=r["filename"],
        upstream_func=r["upstream_func"],
    )
    index_data.append({
        "site": r["river_label"],
        "filename": r["filename"] + ".html",
        "risk": risk,
        "warnings": warnings,
        "lat": r["ref_lat"],
        "lon": r["ref_lon"],
    })
    
# Risk color classes, emoji if desired
def risk_class(risk):
    return {
        "High": "risk-high",
        "Medium": "risk-medium",
        "Low": "risk-low"
    }.get(risk, "")

def risk_emoji(risk):
    return "💩" if risk == "High" else ""
    
report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

table_rows = ""
for entry in index_data:
    table_rows += (
        f"<tr>"
        f"<td>{entry['site']}</td>"
        f"<td class=\"{risk_class(entry['risk'])}\">{entry['risk']} {risk_emoji(entry['risk'])}</td>"
        f"<td><a href=\"{entry['filename']}\">View report</a></td>"
        "</tr>\n"
    )

all_warnings = []
for entry in index_data:
    for w in entry.get("warnings", []):
        all_warnings.append(f"{entry['site']}: {w}")
weather_message_index = "<br>".join(all_warnings)

template_path = os.path.join("templates", "index_template.html")
with open(template_path, "r", encoding="utf-8") as tpl_file:
    tpl = Template(tpl_file.read())

index_html = tpl.substitute(
    report_time=report_time,
    table_rows=table_rows,
    weather_message_index=weather_message_index,
)

with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(index_html)

print("Index page written to docs/index.html")

# --- Generate index.js with map data ---
center_lat = sum(e["lat"] for e in index_data) / len(index_data) if index_data else 0
center_lon = sum(e["lon"] for e in index_data) / len(index_data) if index_data else 0

sites_json = json.dumps([
    {
        "name": e["site"],
        "lat": e["lat"],
        "lon": e["lon"],
        "risk": e["risk"],
        "link": e["filename"],
    }
    for e in index_data
])

js_template_path = os.path.join("templates", "index_js_template.js")
with open(js_template_path, "r", encoding="utf-8") as js_tpl_file:
    js_tpl = Template(js_tpl_file.read())

index_js = js_tpl.substitute(
    center_lat=center_lat,
    center_lon=center_lon,
    sites_json=sites_json,
)

with open("docs/index.js", "w", encoding="utf-8") as f:
    f.write(index_js)

print("Index script written to docs/index.js")
