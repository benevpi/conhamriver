#!/usr/bin/env python3
"""Side-by-side per-day E. coli estimates from every model built for Conham.

Reads the leave-one-out (out-of-sample) prediction CSVs written by the three
modelling approaches and emits a single comparison table:

- distance-band CSO model, weighted toward high counts (`model_conham_ecoli.py`)
- individual-outfall CSO model (`model_conham_ecoli_by_site.py`)
- CSO + rainfall weather model (`weather_conham_ecoli.py`)

All three columns are leave-one-out predictions (each day estimated by a model
that never saw it), so they are directly comparable. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

SOURCES = [
    ("band", "Distance-band CSO (weighted)", "docs/data/conham_ecoli_model_predictions.csv", "loocv_cfu_per_100ml"),
    ("outfall", "Individual-outfall CSO", "docs/data/conham_ecoli_site_model_predictions.csv", "loocv_predicted_cfu_per_100ml"),
    ("cso_rain", "CSO + rainfall", "docs/data/conham_weather_ecoli_predictions.csv", "loocv_predicted_cfu_per_100ml"),
]


def load(path: Path, pred_col: str) -> dict[str, tuple[float, float]]:
    return {
        r["sample_date"]: (float(r["actual_cfu_per_100ml"]), float(r[pred_col]))
        for r in csv.DictReader(path.open(newline="", encoding="utf-8"))
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-csv", default="docs/data/conham_ecoli_model_comparison.csv")
    parser.add_argument("--out-md", default="docs/data/conham_ecoli_model_comparison.md")
    args = parser.parse_args()

    loaded = []
    for key, label, path, col in SOURCES:
        p = Path(path)
        if not p.exists():
            raise SystemExit(f"Missing predictions: {p}. Run that model first.")
        loaded.append((key, label, load(p, col)))

    dates = sorted(loaded[0][2])
    actual = {d: loaded[0][2][d][0] for d in dates}

    rows = []
    errs = {key: [] for key, _, _ in loaded}
    for d in dates:
        row = {"sample_date": d, "actual_cfu_per_100ml": round(actual[d], 1)}
        for key, _, preds in loaded:
            pred = preds[d][1]
            ape = abs(pred - actual[d]) / actual[d] * 100.0
            errs[key].append(ape)
            row[f"{key}_pred"] = round(pred, 1)
            row[f"{key}_abs_pct_error"] = round(ape, 1)
        rows.append(row)

    with Path(args.out_csv).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Conham E. coli: per-day estimates by method",
        "",
        "Every sample day with the out-of-sample (leave-one-out) estimate from each",
        "model. All estimates are CFU/100ml; `e%` is the absolute percentage error.",
        "",
        "| Date | Actual | " + " | ".join(f"{label} | e%" for _, label, _ in loaded) + " |",
        "|---|---:|" + "".join("---:|---:|" for _ in loaded),
    ]
    for row in rows:
        cells = [f"{row['sample_date']}", f"{row['actual_cfu_per_100ml']:.0f}"]
        for key, _, _ in loaded:
            cells.append(f"{row[f'{key}_pred']:.0f}")
            cells.append(f"{row[f'{key}_abs_pct_error']:.0f}%")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Summary error (leave-one-out)")
    lines.append("")
    lines.append("| Model | Median abs % error | Mean abs % error |")
    lines.append("|---|---:|---:|")
    for key, label, _ in loaded:
        lines.append(f"| {label} | {statistics.median(errs[key]):.1f}% | {statistics.mean(errs[key]):.1f}% |")
    lines.extend([
        "",
        "Notes: percentage error is dominated by a few very-low-count days (true value",
        "10-20 CFU/100ml), where any miss is a huge relative error -- the median is the",
        "fairer summary. The individual-outfall model is best overall; the CSO+rainfall",
        "model edges the others on the rain-driven December peaks but rainfall adds no",
        "predictive power beyond the CSO signal on cross-validation.",
        "",
    ])
    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out_csv}")
    print(f"Wrote {args.out_md}")
    for key, label, _ in loaded:
        print(f"  {label:30} median {statistics.median(errs[key]):5.1f}%  mean {statistics.mean(errs[key]):6.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
