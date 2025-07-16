import requests
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2

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

# Reference point
ref_lat = 51.444858
ref_lon = -2.534812

now = datetime.utcnow()
two_days_ago_dt = now - timedelta(days=2)
two_days_ago_str = two_days_ago_dt.strftime("%Y-%m-%d %H:%M:%S")

other_rivers = ['RIVER CHEW', 'charlton bottom via sws', 'bathford brook (s)', 'horsecombe brook']
avon_condition = "(ReceivingWaterCourse = 'RIVER AVON' AND Longitude < -2.527091)"
other_conditions = " OR ".join([f"ReceivingWaterCourse = '{river}'" for river in other_rivers])
all_conditions = avon_condition
if other_conditions:
    all_conditions += " OR " + other_conditions

where_clause = f"({all_conditions}) AND LatestEventStart >= DATE '{two_days_ago_str}'"

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

bands = [1, 5, 10, 20, 50]
band_edges = [1, 5, 10, 20, 50]
band_durations = [0] * len(band_edges)

if data.get("features"):
    for feat in data["features"]:
        attrs = feat["attributes"]
        start = attrs.get('LatestEventStart')
        end = attrs.get('LatestEventEnd')
        lat = attrs.get('Latitude')
        lon = attrs.get('Longitude')
        if start and end and lat is not None and lon is not None:
            duration_seconds = (end - start) / 1000
            dist = haversine(ref_lat, ref_lon, lat, lon)
            for i, edge in enumerate(band_edges):
                lower = 0 if i == 0 else band_edges[i-1]
                if lower < dist <= edge:
                    band_durations[i] += duration_seconds
                    break


# (For risk logic: use band_durations[0] and sum(band_durations[0:2]) for within 1 and within 5)
total_seconds = sum(band_durations)
risk = "Low"
if band_durations[0] > 0 or sum(band_durations[0:2]) > 1800:
    risk = "High"
elif total_seconds == 0:
    risk = "Low"
else:
    risk = "Medium"
    
report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---- HTML RENDER ----

html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Is there poo in Conham River?</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 2em;
            text-align: center;
        }}
        h1 {{ color: #006699; text-align: center; }}
        table {{
            border-collapse: collapse;
            margin: 1.5em auto 0 auto;
            text-align: center;
        }}
        th, td {{
            border: 1px solid #aaa;
            padding: 0.5em 1em;
            text-align: center;
        }}
        th {{ background: #e3f1fa; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        caption {{
            font-weight: bold;
            font-size: 1.1em;
            margin-bottom: 0.5em;
            text-align: center;
        }}
        .risk-high {{ color: red; font-weight: bold; font-size: 2.2em; }}
        .risk-medium {{ color: orange; font-weight: bold; font-size: 2.2em; }}
        .risk-low {{ color: green; font-weight: bold; font-size: 2.2em; }}
        .poo-emoji {{
            font-size: 4em;
            display: block;
            text-align: center;
            margin: 0.3em 0;
        }}
        .disclaimer {{
            font-size: 0.95em;
            color: #444;
            margin-top: 1em;
            text-align: center;
        }}
        .risk-level-line {{
            margin-top: 0.5em;
            margin-bottom: 0.5em;
            font-size: 2.2em;
            font-weight: bold;
        }}
    </style>
</head>
<body>
<h1>Is there poo in Conham River?</h1>
{"<span class='poo-emoji'>💩</span>" if risk == "High" else ""}
<div class="risk-level-line">
    Risk level = <span class="risk-{risk.lower()}">{risk}</span>
</div

<div class="generated-time">Report generated: {report_time}</div>

<table>
    <caption>Total storm overflows upstream of Conham river in the last 48 hours by distance</caption>
    <tr>
        <th>Distance Band</th>
        <th>Total Duration</th>
    </tr>
"""

band_labels = [
    "Within 1 mile",
    "1 to 5 miles",
    "5 to 10 miles",
    "10 to 20 miles",
    "20 to 50 miles"
]

for i, label in enumerate(band_labels):
    hours, minutes = seconds_to_h_m(band_durations[i])
    html += f"<tr><td>{label}</td><td>{hours} hours {minutes} minutes</td></tr>\n"

html += """
</table>
<div class="disclaimer">
    Distances are as the crow flies (not measured along the river or watercourse).
</div>
</body>
</html>
"""


# Write HTML file
import os
os.makedirs("docs", exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("HTML report written to duration_by_distance.html")
