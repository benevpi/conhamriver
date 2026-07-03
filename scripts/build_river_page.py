#!/usr/bin/env python3
"""Build a standalone bacteria + bathing-water-classification page for any river.

Given a sampling CSV with ``sample_date``, ``e_coli_cfu_per_100ml`` and
``intestinal_enterococci_cfu_per_100ml`` columns (the shape produced by
``ea_bathing_water.py`` or ``conham_sampling_2025_2026.csv``), this renders a
self-contained ``docs/<slug>.html`` page: both indicators on one 0-1000 CFU/100ml
panel, with the UK bathing-water class shown as vertical time bands computed the
Directive way (log-normal 95th/90th percentiles of every sample up to each date,
worse of the two indicators). It's the portable version of the bacteria panel on
the Conham 2025 graph.

    python scripts/build_river_page.py --samples docs/data/ilkley_sampling.csv \\
        --title "River Wharfe at Ilkley" --out docs/ilkley.html

The classification logic is imported from build_2025_timeseries so there is one
source of truth. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from build_2025_timeseries import bathing_class, CLASS_RANK, MIN_SAMPLES_TO_CLASSIFY


def _pick(fieldnames: list[str], *must_contain_any) -> str | None:
    """Find a column whose lowercased name contains any of the given substrings."""
    for f in fieldnames:
        low = f.lower()
        if any(s in low for s in must_contain_any):
            return f
    return None


def _to_number(v):
    """Parse a count. Handles '<10', '> 1000', blanks and stray text."""
    if v is None:
        return None
    s = str(v).strip().lstrip("<>=~ ").replace(",", "")
    if not s:
        return None
    num = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
        elif num:
            break
    try:
        return float(num) if num else None
    except ValueError:
        return None


def load_samples(path: Path) -> list[dict]:
    """Read a sampling CSV. Accepts our exact columns *or* the EA/Swimfo download
    headers (e.g. 'Escherichia coli (E. coli) cfu/100ml', 'Sample Date')."""
    with path.open(newline="", encoding="utf-8-sig") as h:
        reader = csv.DictReader(h)
        fields = reader.fieldnames or []
        # date: prefer a header with 'date' but not 'time'; ecoli via 'coli'; ent via 'enteroc'.
        dcol = _pick(fields, "sample_date") or _pick([f for f in fields if "time" not in f.lower()], "date") or _pick(fields, "date")
        ecol = _pick(fields, "e_coli", "e. coli", "escherichia", "coli")
        encol = _pick(fields, "enteroc")
        if not (dcol and ecol and encol):
            raise SystemExit(
                f"Could not identify columns in {path}.\n  headers: {fields}\n"
                f"  matched date={dcol!r} ecoli={ecol!r} enterococci={encol!r}\n"
                "Rename the columns or tell me the header row."
            )
        rows = []
        for r in reader:
            raw = str(r.get(dcol, "")).strip()
            day = raw[:10]
            # Normalise dd/mm/yyyy -> yyyy-mm-dd if needed.
            if "/" in day:
                p = day.split("/")
                if len(p) == 3:
                    day = f"{p[2]}-{p[1].zfill(2)}-{p[0].zfill(2)}"
            if len(day) != 10:
                continue
            rows.append({"d": day, "e": _to_number(r.get(ecol)), "en": _to_number(r.get(encol))})
    rows.sort(key=lambda x: x["d"])
    return rows


def classify(rows: list[dict]) -> None:
    """Attach an expanding-window bathing-water class to each row (in place)."""
    ec_hist, en_hist = [], []
    for row in rows:
        if row["e"] is None or row["en"] is None:
            row["cls"] = None
            continue
        ec_hist.append(row["e"])
        en_hist.append(row["en"])
        row["cls"] = (CLASS_RANK[bathing_class(ec_hist, en_hist)]
                      if len(ec_hist) >= MIN_SAMPLES_TO_CLASSIFY else None)


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — bathing-water quality</title>
<script src="https://cdn.counter.dev/script.js" data-id="99522e23-c138-4047-babb-1e1503dd4a6f" data-utcoffset="1"></script>
<style>
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e7e6e2;--axis:#b8b7b2;--ecoli:#e34948;--ent:#8a5a00;--cap:#b8b7b2;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--grid:#333331;--axis:#4d4d4a;--ecoli:#e66767;--ent:#b5822f;--cap:#4d4d4a;}}
:root[data-theme="dark"]{--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--grid:#333331;--axis:#4d4d4a;--ecoli:#e66767;--ent:#b5822f;--cap:#4d4d4a;}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--surface);color:var(--ink);padding:20px;max-width:960px;margin:0 auto}
h1{font-size:1.35rem;margin:0 0 .2em}
.sub{color:var(--ink2);margin:.2em 0 1em;font-size:.95rem;line-height:1.45}
.controls{display:flex;gap:.5em;margin:.6em 0 .8em;flex-wrap:wrap}
button{font:inherit;font-size:.85rem;padding:.35em .7em;border:1px solid var(--axis);background:transparent;color:var(--ink);border-radius:7px;cursor:pointer}
svg{display:block;width:100%;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}.tick{fill:var(--ink2);font-size:.68rem}
.plabel{font-size:.8rem;font-weight:600;fill:var(--ink)}.punit{font-size:.72rem;fill:var(--ink2)}
.tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--axis);border-radius:8px;padding:.5em .65em;font-size:.78rem;box-shadow:0 4px 14px rgba(0,0,0,.18);z-index:9;min-width:150px}
.tip b{display:block;margin-bottom:.3em}.tip .row{display:flex;justify-content:space-between;gap:1em}
.tip .sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.crosshair{stroke:var(--axis);stroke-width:1;stroke-dasharray:3 3}
.foot{color:var(--ink2);font-size:.8rem;margin-top:1em;line-height:1.4}
table{border-collapse:collapse;font-size:.8rem;margin:.5em 0 1em;width:100%}
th,td{border:1px solid var(--grid);padding:3px 7px;text-align:right}th:first-child,td:first-child{text-align:left}
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="sub">Environment Agency <em>E.&nbsp;coli</em> and intestinal enterococci samples, with the UK bathing-water class as of each sample date (vertical bands). __SUBTITLE__</p>
<div class="controls"><button id="themeBtn" type="button">Toggle dark / light</button><button id="tblBtn" type="button">Show table</button></div>
<div id="panel"></div>
<div id="tableWrap" hidden></div>
<p class="foot">Both indicators are capped at 1000 CFU/100ml (dashed line). The <b>vertical bands</b> are the bathing-water class as of each date — <span style="color:#2ea05c">■</span>&nbsp;Excellent, <span style="color:#b89a12">■</span>&nbsp;Good, <span style="color:#e6822c">■</span>&nbsp;Sufficient, <span style="color:#d64541">■</span>&nbsp;Poor — from the log-normal 95th/90th percentiles of every sample up to that date (worse of the two indicators). Lines break across the off-season gap between bathing seasons. Source: __SOURCE__.</p>
<div id="tip" class="tip" hidden></div>
<script>
const DATA=__DATA__;
const cssv=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const days=DATA.map(d=>d.d);
const t0=Date.parse(days[0]),t1=Date.parse(days[days.length-1]);
const M={l:52,r:14,t:18,b:16},H=190;let W=880;
function xOf(ds){return M.l+(Date.parse(ds)-t0)/(t1-t0)*(W-M.l-M.r);}
function el(t,a){const e=document.createElementNS('http://www.w3.org/2000/svg',t);for(const k in a)e.setAttribute(k,a[k]);return e;}
function txt(x,y,s,a){const e=el('text',Object.assign({x:x,y:y},a));e.textContent=s;return e;}
const CLScol={excellent:'46,160,90',good:'207,168,20',sufficient:'230,126,34',poor:'214,69,65'};
const CLSname={excellent:'Excellent',good:'Good',sufficient:'Sufficient',poor:'Poor'};
const GAP=45*864e5; // break the line across gaps longer than 45 days
function render(){
  const cont=document.getElementById('panel');cont.innerHTML='';W=cont.clientWidth||880;
  const svg=el('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'bacteria'});
  const yBase=H-M.b,yA=(H-M.t-M.b);
  const yScale=v=>M.t+(1-Math.min(v,1000)/1000)*yA;
  [0,250,500,750,1000].forEach(tk=>{const y=yScale(tk);svg.appendChild(el('line',{class:'grid',x1:M.l,y1:y,x2:W-M.r,y2:y}));
    svg.appendChild(txt(M.l-6,y+3,tk,{class:'tick','text-anchor':'end'}));});
  // year gridlines + labels
  let yr=new Date(Date.UTC(new Date(t0).getUTCFullYear(),0,1));
  while(yr.getTime()<=t1){if(yr.getTime()>=t0){const x=xOf(yr.toISOString().slice(0,10));
    svg.appendChild(el('line',{class:'grid',x1:x,y1:M.t,x2:x,y2:yBase,opacity:.6}));
    svg.appendChild(txt(x,H-3,yr.getUTCFullYear(),{class:'tick','text-anchor':'start'}));}
    yr=new Date(Date.UTC(yr.getUTCFullYear()+1,0,1));}
  // classification bands + contiguous-run labels
  const classed=DATA.filter(d=>d.cls);const runs=[];
  classed.forEach((d,i)=>{const x0=xOf(d.d),x1=(i+1<classed.length)?xOf(classed[i+1].d):(W-M.r);
    svg.appendChild(el('rect',{x:x0,y:M.t,width:x1-x0,height:yBase-M.t,fill:'rgba('+CLScol[d.cls]+',0.17)'}));
    if(runs.length&&runs[runs.length-1].cls===d.cls)runs[runs.length-1].x1=x1;else runs.push({cls:d.cls,x0:x0,x1:x1});});
  svg.appendChild(txt(M.l,12,'Bacteria',{class:'plabel'}));
  svg.appendChild(txt(M.l+66,12,'CFU/100ml',{class:'punit'}));
  const yc=yScale(1000);svg.appendChild(el('line',{x1:M.l,y1:yc,x2:W-M.r,y2:yc,stroke:cssv('--cap'),'stroke-width':1,'stroke-dasharray':'4 4'}));
  // two series, line broken across season gaps
  [{k:'e',c:'--ecoli'},{k:'en',c:'--ent'}].forEach(s=>{
    const sc=cssv(s.c);const SS=DATA.filter(d=>d[s.k]!=null);
    let seg=[];const flush=()=>{if(seg.length>1)svg.appendChild(el('polyline',{points:seg.join(' '),fill:'none',stroke:sc,'stroke-width':2,'stroke-linejoin':'round'}));seg=[];};
    for(let i=0;i<SS.length;i++){if(i>0&&Date.parse(SS[i].d)-Date.parse(SS[i-1].d)>GAP)flush();seg.push(xOf(SS[i].d)+','+yScale(SS[i][s.k]));}
    flush();
    SS.forEach(d=>svg.appendChild(el('circle',{cx:xOf(d.d),cy:yScale(d[s.k]),r:3.5,fill:sc,stroke:cssv('--surface'),'stroke-width':1.5})));
  });
  // labels on top
  const bandLabel=(x0,x1,text)=>{const t=txt(0,0,text,{'text-anchor':'middle','font-size':'0.62rem','font-weight':700,fill:cssv('--ink'),stroke:cssv('--surface'),'stroke-width':2.6,'paint-order':'stroke','stroke-linejoin':'round'});
    t.setAttribute('transform','translate('+((x0+x1)/2)+','+((M.t+yBase)/2)+') rotate(-90)');svg.appendChild(t);};
  runs.forEach(r=>{if(r.x1-r.x0>6)bandLabel(r.x0,r.x1,CLSname[r.cls]);});
  const leadX=classed.length?xOf(classed[0].d):(W-M.r);
  if(leadX-M.l>26)bandLabel(M.l,leadX,'Not enough data');
  // legend
  const leg=[['E. coli','--ecoli'],['Ent. enterococci','--ent']];let lx=W-M.r;
  leg.slice().reverse().forEach(([lab,cv])=>{svg.appendChild(txt(lx,12,lab,{class:'punit','text-anchor':'end'}));
    const tw=lab.length*5.6;svg.appendChild(el('rect',{x:lx-tw-13,y:6,width:9,height:9,rx:2,fill:cssv(cv)}));lx-=tw+22;});
  svg.appendChild(el('line',{class:'crosshair',x1:0,y1:M.t,x2:0,y2:yBase,visibility:'hidden'}));
  cont.appendChild(svg);attachHover(cont,yScale);
}
function attachHover(cont,yScale){
  const tip=document.getElementById('tip');
  cont.addEventListener('pointermove',e=>{
    const rect=cont.getBoundingClientRect();const frac=(e.clientX-rect.left-M.l)/(W-M.l-M.r);const tt=t0+frac*(t1-t0);
    let best=null,bd=1e18;DATA.forEach(d=>{if(d.e==null&&d.en==null)return;const dt=Math.abs(Date.parse(d.d)-tt);if(dt<bd){bd=dt;best=d;}});
    const ch=cont.querySelector('.crosshair');
    if(!best||frac<0||frac>1){tip.hidden=true;if(ch)ch.setAttribute('visibility','hidden');return;}
    const x=xOf(best.d);if(ch){ch.setAttribute('x1',x);ch.setAttribute('x2',x);ch.setAttribute('visibility','visible');}
    const rows=[];
    if(best.e!=null)rows.push(['--ecoli','E. coli',best.e.toFixed(0)+' CFU']);
    if(best.en!=null)rows.push(['--ent','Ent. enterococci',best.en.toFixed(0)+' CFU']);
    if(best.cls){rows.push(['rgb('+CLScol[best.cls]+')','Rating',CLSname[best.cls]]);}
    tip.innerHTML='<b>'+new Date(best.d).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})+'</b>'+
      rows.map(r=>'<div class="row"><span><span class="sw" style="background:'+(r[0].startsWith('--')?cssv(r[0]):r[0])+'"></span>'+r[1]+'</span><span>'+r[2]+'</span></div>').join('');
    tip.hidden=false;let tx=e.clientX+14;if(tx+180>window.innerWidth)tx=e.clientX-190;tip.style.left=tx+'px';tip.style.top=(e.clientY+12)+'px';
  });
  cont.addEventListener('pointerleave',()=>{tip.hidden=true;const ch=cont.querySelector('.crosshair');if(ch)ch.setAttribute('visibility','hidden');});
}
function buildTable(){
  const SS=DATA.filter(d=>d.e!=null||d.en!=null);
  let h='<table><tr><th>Date</th><th>E. coli</th><th>Ent. enterococci</th><th>Rating</th></tr>';
  SS.forEach(d=>{h+='<tr><td>'+d.d+'</td><td>'+(d.e==null?'':d.e.toFixed(0))+'</td><td>'+(d.en==null?'':d.en.toFixed(0))+'</td><td>'+(d.cls?CLSname[d.cls]:'')+'</td></tr>';});
  document.getElementById('tableWrap').innerHTML=h+'</table>';
}
document.getElementById('themeBtn').onclick=()=>{const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark');render();};
document.getElementById('tblBtn').onclick=()=>{const w=document.getElementById('tableWrap');w.hidden=!w.hidden;if(!w.hidden&&!w.innerHTML)buildTable();};
render();window.addEventListener('resize',render);
</script>
</body>
</html>
"""


def run(args) -> int:
    rows = load_samples(Path(args.samples))
    if not rows:
        raise SystemExit(f"No samples in {args.samples}")
    classify(rows)
    data = json.dumps(rows, separators=(",", ":"))
    n_class = sum(1 for r in rows if r.get("cls"))
    seasons = sorted({r["d"][:4] for r in rows})
    subtitle = f"{len(rows)} samples across {seasons[0]}–{seasons[-1]}."
    html = (PAGE.replace("__TITLE__", args.title)
                .replace("__SUBTITLE__", subtitle)
                .replace("__SOURCE__", args.source)
                .replace("__DATA__", data))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({len(rows)} samples, {n_class} classified, seasons {seasons[0]}–{seasons[-1]})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--samples", required=True, help="Sampling CSV (sample_date, e_coli_cfu_per_100ml, intestinal_enterococci_cfu_per_100ml)")
    p.add_argument("--title", required=True, help="Page heading, e.g. 'River Wharfe at Ilkley'")
    p.add_argument("--out", required=True, help="Output HTML path, e.g. docs/ilkley.html")
    p.add_argument("--source", default="Environment Agency Bathing Water Quality (environment.data.gov.uk)")
    p.set_defaults(func=run)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
