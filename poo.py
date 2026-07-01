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

# Fetch the daily precipitation forecast from open-meteo.
def get_forecast_rain(lat, lon):
    """Return (forecast, warnings): forecast is a list of (date, mm) for the next
    three days; warnings is the >5mm warning strings for those days."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "forecast_days": 4,  # today + next 3 days
        "timezone": "UTC",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Failed to fetch weather data: {e}")
        return [], []

    today = datetime.utcnow().strftime("%Y-%m-%d")
    times = data.get("daily", {}).get("time", [])
    precip = data.get("daily", {}).get("precipitation_sum", [])
    forecast = [(day, rain or 0.0) for day, rain in zip(times, precip) if day > today][:3]
    warnings = [
        f"{day}: {rain}mm of rain forecast - water quality likely to get worse after this day"
        for day, rain in forecast if rain > 5
    ]
    return forecast, warnings


def get_recent_rain_mm(lat, lon):
    """Total rainfall (mm) over roughly the last 3 days, for the prediction model."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "past_days": 3,
        "forecast_days": 1,
        "timezone": "UTC",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        precip = resp.json().get("daily", {}).get("precipitation_sum", [])
        # Sum the past days (drop the final entry, which is today's forecast).
        past = [p for p in precip[:-1] if p is not None]
        return round(sum(past), 1)
    except Exception as e:
        print(f"Failed to fetch recent rain: {e}")
        return 0.0


# --- Predicted bathing-water quality model ----------------------------------
# Precautionary model mapping recent upstream CSO spilling (+ recent rain) to a
# bathing-water category. Calibrated on 22 Conham E. coli samples (three
# rainfall-independent anomalies excluded). See docs/about_predictions.html for
# why a precautionary rule is used instead of a regression, and the caveats.
QUALITY_ORDER = ["Excellent", "Good", "Sufficient", "Poor"]
# Upper bounds (hours of upstream spilling in the last 7 days) for each category.
QUALITY_SPILL_BOUNDS = [(15.0, "Excellent"), (100.0, "Good"), (500.0, "Sufficient")]
RAIN_WORSEN_MM = 20.0  # heavy recent rain worsens the prediction by one step


def predict_water_quality(spill_hours_7d, recent_rain_mm):
    """Return (category, css_class) from upstream spill hours and recent rain."""
    category = "Poor"
    for bound, label in QUALITY_SPILL_BOUNDS:
        if spill_hours_7d < bound:
            category = label
            break
    if recent_rain_mm >= RAIN_WORSEN_MM:  # rainfall-driven contamination not in CSO data
        idx = min(QUALITY_ORDER.index(category) + 1, len(QUALITY_ORDER) - 1)
        category = QUALITY_ORDER[idx]
    return category, "quality-" + category.lower()


def spill_base_category(spill_hours_7d):
    """Category from spill hours alone (no rain adjustment)."""
    for bound, label in QUALITY_SPILL_BOUNDS:
        if spill_hours_7d < bound:
            return label
    return "Poor"


# Cumulative forecast rain (mm) needed to worsen the forecast by 1/2/3 steps.
FORECAST_RAIN_STEPS = [(40.0, 3), (20.0, 2), (8.0, 1)]


def forecast_water_quality(spill_hours_7d, recent_rain_mm, forecast_rain):
    """Project the category for each of the next 3 days from forecast rainfall.

    Anchored on current spilling (which persists), then worsened by the rain that
    will have fallen by that day (recent rain already down + cumulative forecast).
    Rain is the only forward-looking signal, so the forecast is rain-driven and
    deliberately cautious. Returns a list of (date, mm_that_day, category, css).
    """
    base = spill_base_category(spill_hours_7d)
    rows = []
    cumulative = recent_rain_mm
    for day, rain in forecast_rain:
        cumulative += rain
        steps = 0
        for threshold, s in FORECAST_RAIN_STEPS:
            if cumulative >= threshold:
                steps = s
                break
        idx = min(QUALITY_ORDER.index(base) + steps, len(QUALITY_ORDER) - 1)
        category = QUALITY_ORDER[idx]
        rows.append((day, round(rain, 1), category, "quality-" + category.lower()))
    return rows


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

def generate_report(river_name, river_label, rivers_to_query, ref_lat, ref_lon, filename, upstream_func, watercourse_clause=None):
    now = datetime.utcnow()
    two_days_ago_dt = now - timedelta(days=2)
    two_days_ago_ms = two_days_ago_dt.timestamp() * 1000
    # Query a 7-day window: the last 2 days drive the existing risk/table, and the
    # full 7 days feed the predicted-quality model.
    seven_days_ago_str = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    # Build where clause for the selected rivers. A site may supply a custom
    # watercourse_clause (e.g. Conham matches every River Avon name variant so the
    # close Hanham outfalls are included, while excluding separate brooks).
    conditions = watercourse_clause or " OR ".join(
        [f"ReceivingWaterCourse = '{river}'" for river in rivers_to_query]
    )
    where_clause = f"({conditions}) AND LatestEventStart >= DATE '{seven_days_ago_str}'"

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
    last_cso_end = None
    spill_hours_7d = 0.0  # total upstream spilling within 20 miles over the last 7 days

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
                # 7-day upstream total (within 20 miles) feeds the prediction model.
                if dist <= 20:
                    spill_hours_7d += duration_seconds / 3600
                # The distance-band table / risk use only the last two days.
                if start >= two_days_ago_ms:
                    for i, edge in enumerate(band_edges):
                        lower = 0 if i == 0 else band_edges[i-1]
                        if lower < dist <= edge:
                            band_durations[i] += duration_seconds
                            break
                    if last_cso_end is None or end > last_cso_end:
                        last_cso_end = end

    # Risk calculation as before
    total_seconds = sum(band_durations)
    risk = "Low"
    if band_durations[0] > 0 or sum(band_durations[0:2]) > 1800:
        risk = "High"
    elif total_seconds == 0:
        risk = "Low"
    else:
        risk = "Medium"
    report_time = datetime.now().strftime("%d/%m/%Y at %H:%M")

    table_rows = ""
    for i, label in enumerate(band_labels):
        hours, minutes = seconds_to_h_m(band_durations[i])
        table_rows += f"<tr><td>{label}</td><td>{hours} hours {minutes} minutes</td></tr>\n"

    forecast_rain, warnings = get_forecast_rain(ref_lat, ref_lon)
    weather_message = "<br>".join(warnings) if warnings else ""

    # Predicted bathing-water quality from 7-day upstream spilling + recent rain.
    recent_rain_mm = get_recent_rain_mm(ref_lat, ref_lon)
    predicted_quality, quality_class = predict_water_quality(spill_hours_7d, recent_rain_mm)
    predicted_quality_block = (
        f"<div class='predicted-quality {quality_class}'>"
        f"Predicted water quality: <strong>{predicted_quality}</strong>"
        f" <a class='about-predictions' href='about_predictions.html'>(how is this predicted?)</a>"
        f"</div>"
        f"<div class='prediction-basis'>Based on {spill_hours_7d:.0f} hours of upstream spilling "
        f"in the last 7 days and {recent_rain_mm:.0f}mm of recent rain.</div>"
    )

    # Three-day outlook: project the category forward using the rain forecast.
    forecast_rows = forecast_water_quality(spill_hours_7d, recent_rain_mm, forecast_rain)
    if forecast_rows:
        forecast_cells = "".join(
            f"<tr><td>{day}</td><td>{rain:.0f}mm</td>"
            f"<td class='{css}'>{category}</td></tr>\n"
            for day, rain, category, css in forecast_rows
        )
        forecast_block = (
            "<table class='forecast-table'>"
            f"<caption>Forecast water quality for the next {len(forecast_rows)} days "
            "(from the rain forecast)</caption>"
            "<tr><th>Date</th><th>Rain forecast</th><th>Predicted quality</th></tr>"
            f"{forecast_cells}</table>"
            "<div class='prediction-basis'>Forecast is rain-driven and cautious: rain is "
            "assumed to trigger upstream spilling. "
            "<a href='about_predictions.html'>How this works.</a></div>"
        )
    else:
        forecast_block = ""

    risk_note_block = ""
    safe_time = None
    if risk in ("Medium", "High") and last_cso_end:
        last_end_dt = datetime.utcfromtimestamp(last_cso_end / 1000)
        safe_dt = last_end_dt + timedelta(hours=48)
        safe_time = safe_dt.strftime('%d/%m/%Y at %H:%M')
        risk_note_block = (
            f"<div class='risk-note'>If there is no further rain, the risk will be low at {safe_time} UTC</div>"
        )

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
        risk_note_block=risk_note_block,
        predicted_quality_block=predicted_quality_block,
        forecast_block=forecast_block,
    )

    os.makedirs("docs", exist_ok=True)
    with open(f"docs/{filename}.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML report written to docs/{filename}.html")
    return risk, warnings, safe_time, predicted_quality, quality_class

# --- Define rivers/reports you want to generate ---
reports = [
    {
        "river_name": "conham",
        "river_label": "Avon at Conham River",
        "rivers_to_query": [
            'RIVER AVON', 'RIVER CHEW', 'charlton bottom via sws', 'bathford brook (s)', 'horsecombe brook', 'river avon via sws', 'river avon (via sws)'
        ],
        # Match every River Avon name variant so the close Hanham outfalls (on
        # names like "RIVER AVON(E)" / "RIVER AVON (E) VIA SWS") are counted, plus
        # the Chew and the brooks that join upstream. Warmley/Siston brooks are
        # deliberately excluded: they drain to a separate catchment that does not
        # join the Avon above Conham. Three LIKE casings cover the feed's mixed case.
        "watercourse_clause": (
            "ReceivingWaterCourse LIKE '%AVON%' "
            "OR ReceivingWaterCourse LIKE '%avon%' "
            "OR ReceivingWaterCourse LIKE '%Avon%' "
            "OR ReceivingWaterCourse = 'RIVER CHEW' "
            "OR ReceivingWaterCourse = 'charlton bottom via sws' "
            "OR ReceivingWaterCourse = 'bathford brook (s)' "
            "OR ReceivingWaterCourse = 'horsecombe brook'"
        ),
        "ref_lat": 51.444858,
        "ref_lon": -2.534812,
        "filename": "conham",
        "upstream_func": is_upstream_conham,
    },
    {
        "river_name": "salford",
        "river_label": "Avon at Salford",
        "rivers_to_query": [
            'RIVER AVON', 'bathford brook (s)', 'horsecombe brook', 'river avon via sws', 'river avon (via sws)'
        ],
        "ref_lat": 51.398639,
        "ref_lon": -2.446917,
        "filename": "salford",
        "upstream_func": is_upstream_salford,
    },
    {
        "river_name": "warleigh",
        "river_label": "Avon at Warleigh Weir",
        "rivers_to_query": [
            'RIVER AVON', 'bathford brook (s)','Bristol Avon', 'River Frome', 'river avon via sws', 'river avon (via sws)'
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
        "ref_lat": 51.375278,
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
    risk, warnings, safe_time, predicted_quality, quality_class = generate_report(
        river_name=r["river_name"],
        river_label=r["river_label"],
        rivers_to_query=r["rivers_to_query"],
        ref_lat=r["ref_lat"],
        ref_lon=r["ref_lon"],
        filename=r["filename"],
        upstream_func=r["upstream_func"],
        watercourse_clause=r.get("watercourse_clause"),
    )
    index_data.append({
        "site": r["river_label"],
        "filename": r["filename"] + ".html",
        "risk": risk,
        "warnings": warnings,
        "safe_time": safe_time,
        "predicted_quality": predicted_quality,
        "quality_class": quality_class,
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
    
report_time = datetime.now().strftime("%d/%m/%Y at %H:%M")

table_rows = ""
for entry in index_data:
    clear_time = entry['safe_time'] or ("now" if entry['risk'] == "Low" else "")
    table_rows += (
        f"<tr>"
        f"<td>{entry['site']}</td>"
        f"<td class=\"{risk_class(entry['risk'])}\">{entry['risk']} {risk_emoji(entry['risk'])}</td>"
        f"<td class=\"{entry['quality_class']}\">{entry['predicted_quality']}</td>"
        f"<td>{clear_time}</td>"
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
