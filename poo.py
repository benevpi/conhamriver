import requests
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2
import os

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

    # HTML generation
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Is there poo in {river_label}?</title>
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
        .generated-time {{
            font-size: 1.1em;
            color: #333;
            margin-bottom: 1em;
            margin-top: 0;
            text-align: center;
        }}
    </style>
</head>
<body>
<h1>Is there poo in {river_label}?</h1>
{"<span class='poo-emoji'>💩</span>" if risk == "High" else ""}
<div class="risk-level-line">
    Risk level = <span class="risk-{risk.lower()}">{risk}</span>
</div>

<div class="generated-time">Report generated: {report_time}. If if has rained since then, the data may be inaccurate</div>
</br>
<div class="generated-time">The risk is based entirely on the author's personal risk tolerance. River swimming is never 100% safe, so it's up to you to make an informed decision </div>
</br>
<div class="generated-time">This system is currently being tested and may produce unexpected or inaccurate results, but it's trying it's hardest</div>



<table>
    <caption>Total storm overflows in the last two days upstream of {river_label} by distance</caption>
    <tr>
        <th>Distance Band</th>
        <th>Total Duration</th>
    </tr>
    """
    for i, label in enumerate(band_labels):
        hours, minutes = seconds_to_h_m(band_durations[i])
        html += f"<tr><td>{label}</td><td>{hours} hours {minutes} minutes</td></tr>\n"
    html += """
</table>
<div class="disclaimer">
    This tool uses a simplified "upstream" test based on longitude and/or latitude. For Conham, CSOs east of the site are counted as upstream. This may include or miss some actual upstream sources, especially in complex river sections.
    <br>Distances are as the crow flies (not measured along the river or watercourse).
</div>
</body>
</html>
"""
    # Save to docs/filename
    os.makedirs("docs", exist_ok=True)
    with open(f"docs/{filename}.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML report written to docs/{filename}.html")
    return risk

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
        "ref_lat": 51.444858,
        "ref_lon": -2.534812,
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
        "ref_lat": 51.415847,
        "ref_lon": -2.497921,
        "filename": "chew",
        "upstream_func": is_upstream_chew,
    },
    # Add more dicts here for other rivers/sites as needed
]

for r in reports:
    risk = generate_report(
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
        "risk": risk
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
    
index_html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Is there poo in the river?</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 2em; text-align: center; }
        table { border-collapse: collapse; margin: 2em auto 0 auto; }
        th, td { border: 1px solid #aaa; padding: 0.7em 1.5em; text-align: center; font-size: 1.2em; }
        th { background: #e3f1fa; }
        tr:nth-child(even) { background: #f9f9f9; }
        .risk-high { color: red; font-weight: bold; font-size: 1.5em; }
        .risk-medium { color: orange; font-weight: bold; font-size: 1.2em; }
        .risk-low { color: green; font-weight: bold; font-size: 1.2em; }
        .poo-emoji { font-size: 2em; vertical-align: middle; }
        caption { font-size: 1.3em; font-weight: bold; margin-bottom: 1em; }
    </style>
</head>
<body>
    <h1>Is there poo in the river?</h1>
    <table>
        <caption>Swim sites and current risk</caption>
        <tr>
            <th>Site</th>
            <th>Risk Level</th>
            <th>Details</th>
        </tr>
"""

for entry in index_data:
    index_html += f"""<tr>
        <td>{entry['site']}</td>
        <td class="{risk_class(entry['risk'])}">{entry['risk']} {risk_emoji(entry['risk'])}</td>
        <td><a href="{entry['filename']}">View report</a></td>
    </tr>
    """

index_html += """
    </table>
    <div style="margin-top:1.5em;font-size:0.95em;color:#444;">
        Reports are based on storm overflow data and automated "upstream" calculations for each site. See detailed reports for logic and disclaimers.
    </div>
</body>
</html>
"""

with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(index_html)

print("Index page written to docs/index.html")
