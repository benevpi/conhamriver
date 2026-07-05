#!/usr/bin/env python3
"""Prototype: river-travel-time / die-off-weighted CSO risk vs the current
straight-line distance model, for Conham and Salford.

The live site scores risk from raw upstream spill *hours* bucketed by
as-the-crow-flies distance. That treats a spill 15 miles up a side brook as
equal to one 0.6 miles up the main stem. This prototype instead weights each
outfall by an estimate of how much of its E. coli load survives the journey
downstream to the swim site:

    river_miles  ~= crow_miles * SINUOSITY   (main stem meanders)
                    * TRIB_PENALTY            (extra, if off the main stem)
    weight       = 10 ** (-river_miles / D90) (T90 die-off every D90 river-mi)
    score        = spill_hours * weight       (summed over upstream outfalls)

Parameters are illustrative, not calibrated — the point is the *shape*, not a
precise number. Uses docs/data/conham_nearby_cso_events.csv (whole-catchment
2025 events with outfall coordinates). Standard library only.
"""
import csv, math
from datetime import datetime

SINUOSITY   = 1.7     # lower Bristol Avon meanders a lot between Bath and Bristol
TRIB_PENALTY= 1.5     # off-main-stem outfalls travel down a tributary first
MONTHS      = {5,6,7,8,9}   # bathing season

def hav(a,b,c,d):
    R=3958.8;p1,p2=math.radians(a),math.radians(c)
    dp=math.radians(c-a);dl=math.radians(d-b)
    x=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R*2*math.atan2(math.sqrt(x),math.sqrt(1-x))

def bristol_ds(wc,lat):
    wc=wc.upper()
    if any(k in wc for k in ('HORFIELD','TRYM','BRISLINGTON','WARMLEY','SISTON')):return True
    if 'FROME' in wc and lat>51.45:return True
    return False
def is_chew(wc):return 'CHEW' in wc.upper() or 'WINFORD' in wc.upper()

SITES={
 'Conham' :dict(lat=51.444858,lon=-2.534812,
                up=lambda lat,lon,wc:(is_chew(wc) or (not bristol_ds(wc,lat) and lon>-2.534812))),
 'Salford':dict(lat=51.398639,lon=-2.446917,
                up=lambda lat,lon,wc:(not bristol_ds(wc,lat) and lon>-2.457616)),
}

def river_miles(crow,wc):
    rm=crow*SINUOSITY
    if 'AVON' not in wc.upper(): rm*=TRIB_PENALTY   # tributary (brook / Chew)
    return rm

def load():
    site={}
    with open('docs/data/conham_nearby_cso_events.csv') as f:
        for r in csv.DictReader(f):
            try:
                lat=float(r['outfall_lat']);lon=float(r['outfall_lon']);h=float(r['duration_hours'])
                m=datetime.fromisoformat(r['event_start']).month
            except Exception:continue
            if m not in MONTHS:continue
            sid=r['site_id']
            site.setdefault(sid,[r['site_name'],lat,lon,0.0,r['receiving_watercourse']]);site[sid][3]+=h
    return list(site.values())

ALL=load()

def analyse(name,D90):
    cfg=SITES[name]
    up=[r for r in ALL if cfg['up'](r[1],r[2],r[4])]
    recs=[]
    for nm,lat,lon,h,wc in up:
        crow=hav(cfg['lat'],cfg['lon'],lat,lon)
        rm=river_miles(crow,wc)
        w=10**(-rm/D90)
        recs.append((nm,crow,rm,h,w,h*w))
    raw=sum(x[3] for x in recs)      # current-model proxy: raw upstream hours
    eff=sum(x[5] for x in recs)      # die-off-weighted score
    return recs,raw,eff

print("Die-off-weighted risk score vs current raw-upstream-hours (May-Sep 2025)\n")
print(f"{'D90 (river-mi for 90% die-off)':<34}{'Conham':>12}{'Salford':>12}")
for D90 in (6,12,24):
    _,rc,ec=analyse('Conham',D90); _,rs,es=analyse('Salford',D90)
    print(f"  effective score, D90={D90:<2} mi{'':14}{ec:12.1f}{es:12.1f}")
print(f"  raw upstream hours (current){'':6}{analyse('Conham',12)[1]:12.1f}{analyse('Salford',12)[1]:12.1f}")

for name in ('Conham','Salford'):
    recs,raw,eff=analyse(name,12)
    recs.sort(key=lambda x:-x[5])
    print(f"\n=== {name}: top contributors under die-off model (D90=12 river-mi) ===")
    print(f"{'wt-hrs':>7}{'raw h':>7}{'crow':>6}{'river':>6}{'keep%':>6}  outfall")
    for nm,crow,rm,h,w,wh in recs[:10]:
        print(f"{wh:7.1f}{h:7.0f}{crow:6.1f}{rm:6.1f}{w*100:6.1f}  {nm[:40]}")
    # share captured by the near cluster (<=3 crow-mi, main stem/Chew mouth)
    near=sum(wh for nm,crow,rm,h,w,wh in recs if crow<=3)
    nearraw=sum(h for nm,crow,rm,h,w,wh in recs if crow<=3)
    print(f"  <=3 crow-mi share:  raw {nearraw/raw*100:4.0f}%  ->  die-off-weighted {near/eff*100:4.0f}%")
