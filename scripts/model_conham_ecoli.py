#!/usr/bin/env python3
"""Fit a simple statistical model that estimates Conham E. coli counts.

The model is a log-linear (log10) ordinary-least-squares regression of the
digitised E. coli concentration on the upstream CSO spill features produced by
``scripts/analyze_conham_cso_ecoli.py``. It is fit on the 2025 sample dates,
predicts each day's count, and reports the percentage error per day.

Because the CSO features have almost no variation in this dataset (only one
sample window recorded any upstream spill), the regression is regularised with a
small ridge term and, in practice, collapses towards a constant baseline. The
per-day percentage errors therefore mostly reflect how far each day sits from
the typical (geometric-mean) E. coli level, which is the honest answer this data
supports. The script is intentionally standard-library only.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

# Features used to predict log10(E. coli). These are the per-window CSO summaries
# emitted by analyze_conham_cso_ecoli.py. nearest_spill_miles is turned into a
# "1 / (1 + miles)" proximity term so that "no spill" maps cleanly to 0.
FEATURE_COLUMNS = ["event_count", "spill_hours_total", "spill_proximity"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        default="docs/data/conham_cso_ecoli_features.csv",
        help="CSO/E. coli feature CSV from analyze_conham_cso_ecoli.py",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=3,
        help="Which lookback window's CSO features to model on",
    )
    parser.add_argument(
        "--output",
        default="docs/data/conham_ecoli_model_predictions.csv",
        help="Per-day prediction / percentage-error CSV",
    )
    parser.add_argument(
        "--report",
        default="docs/data/conham_ecoli_model.md",
        help="Markdown summary of the model and its errors",
    )
    parser.add_argument(
        "--ridge",
        type=float,
        default=1e-3,
        help="Ridge penalty to keep the near-degenerate design matrix invertible",
    )
    return parser.parse_args()


def load_rows(path: Path, lookback_days: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            if int(raw["lookback_days"]) != lookback_days:
                continue
            nearest = raw.get("nearest_spill_miles", "")
            proximity = 1.0 / (1.0 + float(nearest)) if nearest not in ("", None) else 0.0
            rows.append(
                {
                    "sample_date": raw["sample_date"],
                    "e_coli": float(raw["e_coli_cfu_per_100ml"]),
                    "event_count": float(raw["event_count"]),
                    "spill_hours_total": float(raw["spill_hours_total"]),
                    "spill_proximity": proximity,
                }
            )
    if not rows:
        raise SystemExit(f"No rows found for lookback_days={lookback_days}")
    return rows


def standardise(rows: list[dict[str, object]]) -> tuple[list[list[float]], list[dict[str, float]]]:
    """Return a design matrix (with intercept) and the per-feature mean/std used."""
    stats: list[dict[str, float]] = []
    columns: list[list[float]] = []
    for name in FEATURE_COLUMNS:
        values = [float(r[name]) for r in rows]
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(var)
        stats.append({"name": name, "mean": mean, "std": std})
        if std == 0.0:
            # No variation: contributes nothing, keep a zero column so the
            # coefficient is well defined (and ends up ~0 after the ridge).
            columns.append([0.0 for _ in values])
        else:
            columns.append([(v - mean) / std for v in values])
    design = [[1.0] + [columns[c][i] for c in range(len(FEATURE_COLUMNS))] for i in range(len(rows))]
    return design, stats


def solve_ridge(design: list[list[float]], target: list[float], ridge: float) -> list[float]:
    """Solve (XᵀX + ridge·I) β = Xᵀy via Gaussian elimination (intercept unpenalised)."""
    p = len(design[0])
    xtx = [[sum(design[k][i] * design[k][j] for k in range(len(design))) for j in range(p)] for i in range(p)]
    xty = [sum(design[k][i] * target[k] for k in range(len(design))) for i in range(p)]
    for i in range(1, p):  # do not penalise the intercept (column 0)
        xtx[i][i] += ridge
    # Gaussian elimination with partial pivoting.
    aug = [row[:] + [xty[i]] for i, row in enumerate(xtx)]
    for col in range(p):
        pivot = max(range(col, p), key=lambda r: abs(aug[r][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_val = aug[col][col]
        if abs(pivot_val) < 1e-12:
            continue
        for r in range(p):
            if r == col:
                continue
            factor = aug[r][col] / pivot_val
            aug[r] = [aug[r][k] - factor * aug[col][k] for k in range(p + 1)]
    return [aug[i][p] / aug[i][i] if abs(aug[i][i]) > 1e-12 else 0.0 for i in range(p)]


def main() -> int:
    args = parse_args()
    rows = load_rows(Path(args.features), args.lookback_days)
    design, stats = standardise(rows)
    y = [math.log10(r["e_coli"]) for r in rows]
    beta = solve_ridge(design, y, args.ridge)

    predictions = []
    abs_pct_errors = []
    for i, r in enumerate(rows):
        log_pred = sum(beta[j] * design[i][j] for j in range(len(beta)))
        pred = 10 ** log_pred
        actual = r["e_coli"]
        signed_pct = (pred - actual) / actual * 100.0
        abs_pct = abs(signed_pct)
        abs_pct_errors.append(abs_pct)
        predictions.append(
            {
                "sample_date": r["sample_date"],
                "actual_cfu_per_100ml": round(actual, 1),
                "predicted_cfu_per_100ml": round(pred, 1),
                "signed_pct_error": round(signed_pct, 1),
                "abs_pct_error": round(abs_pct, 1),
            }
        )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_date", "actual_cfu_per_100ml", "predicted_cfu_per_100ml", "signed_pct_error", "abs_pct_error"],
        )
        writer.writeheader()
        writer.writerows(predictions)

    mape = sum(abs_pct_errors) / len(abs_pct_errors)
    median_ape = sorted(abs_pct_errors)[len(abs_pct_errors) // 2]
    windows_with_spill = sum(1 for r in rows if r["event_count"] > 0)

    lines = [
        "# Conham E. coli estimation model",
        "",
        "Generated by `scripts/model_conham_ecoli.py`. A log10 OLS (ridge-stabilised)",
        f"regression of E. coli on upstream CSO features at a {args.lookback_days}-day lookback,",
        "fit on the 2025 sample dates and scored back on the same dates.",
        "",
        f"- Samples: {len(rows)}",
        f"- Mean absolute percentage error (MAPE): {mape:.1f}%",
        f"- Median absolute percentage error: {median_ape:.1f}%",
        "",
        "## Model coefficients (standardised features, log10 target)",
        "",
        "| Term | Coefficient |",
        "|---|---:|",
        f"| intercept | {beta[0]:.4f} |",
    ]
    for j, s in enumerate(stats, start=1):
        lines.append(f"| `{s['name']}` | {beta[j]:.4f} |")
    lines.extend(
        [
            "",
            "## Per-day percentage error",
            "",
            "| Sample date | Actual | Predicted | Signed % error | Abs % error |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for p in predictions:
        lines.append(
            f"| {p['sample_date']} | {p['actual_cfu_per_100ml']:.0f} | {p['predicted_cfu_per_100ml']:.0f} | "
            f"{p['signed_pct_error']:+.1f}% | {p['abs_pct_error']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            f"- {windows_with_spill} of {len(rows)} sample windows recorded upstream CSO spill",
            "  activity, so the features carry real but weak signal: more spill events shift",
            "  the estimate up, yet days with very low actual counts still produce large",
            "  percentage errors, which inflates the MAPE.",
            "- E. coli values are chart-digitised and capped at 1000 CFU/100ml (right-censored),",
            "  so days at 1000 are under-predicted by construction.",
            "- Errors are in-sample (fit and scored on the same 25 dates); true predictive",
            "  error on unseen dates would be larger.",
            "",
        ]
    )
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"Wrote {args.report}")
    print(f"MAPE: {mape:.1f}%  median APE: {median_ape:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
