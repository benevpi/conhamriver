#!/usr/bin/env python3
"""Fetch E. coli + intestinal enterococci samples for an EA designated bathing water.

Pulls the Environment Agency's in-season sample results (the same two indicators
as the Conham data -- Escherichia coli and intestinal enterococci) from the
Bathing Water Quality linked-data API, and writes them in the *same CSV shape* as
``conham_sampling_2025_2026.csv`` so the rest of the pipeline
(`build_2025_timeseries.py`, the classification, the graph) can reuse it.

Default target is the **River Wharfe at Ilkley** -- the first designated river
bathing water (2020), so it has several bathing seasons of data, enough for the
percentile classification to be meaningful (unlike Conham's single season).

Finding a bathing water's id
----------------------------
Open the site on Swimfo (https://environment.data.gov.uk/bwq/profiles/) and read
the ``eubwid`` from the profile URL, e.g.
``.../profile.html?site=ukl1602-36700`` -> id ``ukl1602-36700``. Pass it with
``--eubwid``. The default below is Ilkley; **verify it** on Swimfo before trusting
the output.

Network
-------
Needs outbound access to ``environment.data.gov.uk``. Run where that is allowed
and commit the CSV:

    python scripts/ea_bathing_water.py fetch --eubwid ukl1602-36700 \\
        --out docs/data/ilkley_sampling.csv --years 2021-2025

If the API's JSON field names differ from what this script expects (the EA
platform occasionally shifts them), run with ``--debug`` to dump the first raw
sample item so the determinand keys can be adjusted.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://environment.data.gov.uk/data/bathing-water-quality/in-season.json"
ID_BASE = "https://environment.data.gov.uk/id/bathing-water/"
DEFAULT_EUBWID = "ukl1602-36700"  # River Wharfe at Ilkley -- VERIFY on Swimfo
SOURCE = "https://environment.data.gov.uk/ (EA Bathing Water Quality)"

# Candidate keys for each determinand, in case the API labels them differently.
ECOLI_KEYS = ["escherichiaColi", "escherichiaColiCount", "eColi", "ecoli"]
ENT_KEYS = ["intestinalEnterococci", "intestinalEnterococciCount", "enterococci"]


def _num_and_qualifier(node):
    """Pull a numeric count and any < / > qualifier out of a determinand node.

    The node may be a bare number, or an object like {"count": 10} /
    {"result": 10} possibly with {"qualifier": {"label": "<"}}. Returns
    (value:str, qualifier:str) with '' when absent.
    """
    if node is None:
        return "", ""
    if isinstance(node, (int, float)):
        return str(int(node)), ""
    if isinstance(node, str):
        return node.strip(), ""
    if isinstance(node, dict):
        val = ""
        for k in ("count", "result", "value", "concentration"):
            if node.get(k) not in (None, ""):
                val = str(node[k])
                break
        qual = ""
        q = node.get("qualifier")
        if isinstance(q, dict):
            lab = q.get("label") or q.get("notation") or ""
            qual = (lab[0] if isinstance(lab, list) and lab else lab) or ""
        elif isinstance(q, str):
            qual = q
        return val, str(qual)
    return "", ""


def _get_determinand(item, keys):
    for k in keys:
        if k in item:
            return _num_and_qualifier(item[k])
    return "", ""


def _date_of(item):
    dt = item.get("sampleDateTime")
    if isinstance(dt, dict):
        dt = dt.get("inXSDDateTime") or dt.get("label") or ""
    if isinstance(dt, list) and dt:
        dt = dt[0]
    return str(dt)[:10] if dt else ""


def fetch_year(eubwid: str, year: int, page_size: int) -> list[dict]:
    params = {
        "year": str(year),
        "samplingPoint.bathingWater": ID_BASE + eubwid,
        "_pageSize": str(page_size),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.load(response)
    result = data.get("result", data)
    items = result.get("items", []) if isinstance(result, dict) else []
    return items


def _try(url: str, snippet: int = 1200) -> tuple[int, str]:
    """GET a URL, returning (status, body-snippet-or-json-summary)."""
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        b = exc.read().decode("utf-8", "replace")
        import re
        m = re.search(r"<pre>(.*?)</pre>", b, re.S)
        return exc.code, (m.group(1).strip() if m else b[:snippet])
    except urllib.error.URLError as exc:
        return 0, f"URLError: {exc}"
    try:
        data = json.loads(body)
        result = data.get("result", data)
        items = result.get("items", []) if isinstance(result, dict) else []
        if items:
            keys = sorted(items[0].keys())
            return 200, f"OK, {len(items)} item(s). first-item keys: {keys}\nfirst item: {json.dumps(items[0])[:1100]}"
        # No items -> show what endpoints/links the resource advertises.
        return 200, f"OK, no item list. top-level keys: {sorted(data.keys()) if isinstance(data,dict) else type(data)}\n{json.dumps(data)[:snippet]}"
    except json.JSONDecodeError:
        return 200, "OK but not JSON: " + body[:snippet]


def run_probe(args) -> int:
    """Discover Ilkley's eubwid/sampling point and the in-season filter/field names."""
    q = urllib.parse.quote
    bw = "https://environment.data.gov.uk/data/bathing-water"
    isn = "https://environment.data.gov.uk/data/bathing-water-quality/in-season.json"
    guess = ID_BASE + args.eubwid
    candidates = [
        ("1 bathing-water list shape (_pageSize=1)", f"{bw}.json?_pageSize=1"),
        ("2 find Ilkley by name", f"{bw}.json?name={q('River Wharfe at Ilkley')}&_pageSize=3"),
        ("3 find Ilkley by search", f"{bw}.json?_search=Ilkley&_pageSize=3"),
        ("4 in-season bare list (reveals sample fields?)", f"{isn}?_pageSize=2"),
        ("5 in-season, samplingPoint.bathingWater filter", f"{isn}?samplingPoint.bathingWater={guess}&_pageSize=2"),
        ("6 in-season, _view=all", f"{isn}?_view=all&_pageSize=2"),
    ]
    for name, url in candidates:
        code, info = _try(url, snippet=1600)
        print(f"\n### {name}\n{url}\n-> HTTP {code}\n{info}")
    print("\nPaste this back. #2/#3 give Ilkley's eubwid + samplingPoint; #4/#5/#6 reveal the sample field names and the working filter.")
    return 0


def run_fetch(args) -> int:
    lo, hi = (args.years.split("-") + [args.years])[:2]
    years = range(int(lo), int(hi) + 1)
    rows = {}
    first_item = None
    for year in years:
        try:
            items = fetch_year(args.eubwid, year, args.page_size)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:600]
            raise SystemExit(
                f"EA API returned HTTP {exc.code} for:\n  {API}?...year={year}...\n{body}\n\n"
                "The query shape is wrong for this API. Run `python scripts/ea_bathing_water.py "
                "probe` and paste the output so the endpoint can be corrected."
            )
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"Could not reach the EA API ({API}): {exc}.\n"
                "Run where environment.data.gov.uk egress is allowed, then commit "
                f"{args.out}."
            )
        for it in items:
            if first_item is None:
                first_item = it
            date = _date_of(it)
            if not date:
                continue
            ec, ecq = _get_determinand(it, ECOLI_KEYS)
            en, enq = _get_determinand(it, ENT_KEYS)
            note = " ".join(p for p in [f"E.coli {ecq}1000+" if ecq in (">", ">=") else "",
                                        f"ent {enq}1000+" if enq in (">", ">=") else ""] if p).strip()
            rows[date] = {
                "sample_date": date,
                "e_coli_cfu_per_100ml": ec,
                "intestinal_enterococci_cfu_per_100ml": en,
                "value_note": note,
                "source": SOURCE,
            }
        print(f"  {year}: {len(items)} samples")

    if args.debug and first_item is not None:
        print("\n--- first raw sample item (for field-name checking) ---")
        print(json.dumps(first_item, indent=2)[:2000])

    if not rows:
        raise SystemExit(
            "No samples parsed. Check the --eubwid is correct (see Swimfo) and, if the "
            "API returned data, re-run with --debug to inspect the determinand field names."
        )

    ordered = [rows[d] for d in sorted(rows)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as h:
        writer = csv.DictWriter(h, fieldnames=list(ordered[0].keys()))
        writer.writeheader()
        writer.writerows(ordered)
    got_ec = sum(1 for r in ordered if r["e_coli_cfu_per_100ml"])
    got_en = sum(1 for r in ordered if r["intestinal_enterococci_cfu_per_100ml"])
    print(f"Wrote {out} ({len(ordered)} sample dates; {got_ec} with E. coli, {got_en} with enterococci)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    f = sub.add_parser("fetch", help="Fetch EA in-season samples (needs network)")
    f.add_argument("--eubwid", default=DEFAULT_EUBWID, help="EA bathing water id (see Swimfo profile URL)")
    f.add_argument("--out", default="docs/data/ilkley_sampling.csv")
    f.add_argument("--years", default="2021-2025", help="Year range, e.g. 2021-2025")
    f.add_argument("--page-size", type=int, default=500)
    f.add_argument("--debug", action="store_true", help="Dump the first raw item for field-name checking")
    f.set_defaults(func=run_fetch)

    pr = sub.add_parser("probe", help="Try candidate API endpoint shapes and report which works")
    pr.add_argument("--eubwid", default=DEFAULT_EUBWID)
    pr.add_argument("--year", default="2023")
    pr.set_defaults(func=run_probe)
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
