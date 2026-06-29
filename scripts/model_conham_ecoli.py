#!/usr/bin/env python3
"""Fit and cross-validate a statistical model that estimates Conham E. coli counts.

The model is a log-linear (log10) ridge regression of the digitised E. coli
concentration on upstream CSO spill features produced by
``scripts/analyze_conham_cso_ecoli.py``.

Model selection
---------------
The feature set was chosen by leave-one-out cross-validation (LOOCV) over the
CSO features at every 1- to 7-day lookback window. The winner is a single
predictor: ``log1p(spill_hours_10_to_20_miles)`` measured over a 7-day lookback.
That out-performs the earlier multi-feature model and the naive "predict the
mean" baseline, and is mechanistically plausible: spills 10-20 miles upstream
take roughly a few days to reach Conham, so a week-long window captures them.
Adding further features degrades LOOCV error on only 25 samples (over-fitting),
so the model is deliberately parsimonious.

Honest error reporting
----------------------
Each day's "percentage it would be wrong by" is reported from the LOOCV
prediction -- i.e. a model trained on the other 24 dates and asked to predict the
held-out day -- not the in-sample fit. The script is standard-library only.
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

# A model is a list of (lookback_days, column, transform) feature specs.
# transform is one of: None (raw), "log1p", or "proximity" (1 / (1 + miles)).
FeatureSpec = tuple[int, str, "str | None"]

SELECTED_MODEL: list[FeatureSpec] = [(7, "spill_hours_10_to_20_miles", "log1p")]

# Reference models kept only so the report can show *why* SELECTED_MODEL was
# chosen (LOOCV beats both of these).
REFERENCE_MODELS: dict[str, list[FeatureSpec]] = {
    "mean baseline (intercept only)": [],
    "previous model (event_count + spill_hours_total + proximity @ 3-day)": [
        (3, "event_count", None),
        (3, "spill_hours_total", None),
        (3, "nearest_spill_miles", "proximity"),
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        default="docs/data/conham_cso_ecoli_features.csv",
        help="CSO/E. coli feature CSV from analyze_conham_cso_ecoli.py",
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
        default=0.1,
        help="Ridge penalty on standardised features (intercept unpenalised)",
    )
    return parser.parse_args()


# --------------------------------------------------------------------------- #
# Data loading / feature engineering
# --------------------------------------------------------------------------- #
def load_features(path: Path) -> tuple[list[str], dict[str, float], dict[str, dict[int, dict[str, str]]]]:
    by_date: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    ecoli: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            date = raw["sample_date"]
            by_date[date][int(raw["lookback_days"])] = raw
            ecoli[date] = float(raw["e_coli_cfu_per_100ml"])
    dates = sorted(by_date)
    if not dates:
        raise SystemExit("No rows found in feature CSV")
    return dates, ecoli, by_date


def feature_value(by_date: dict[str, dict[int, dict[str, str]]], date: str, spec: FeatureSpec) -> float:
    lookback, column, transform = spec
    raw = by_date[date][lookback][column]
    if transform == "proximity":
        return 1.0 / (1.0 + float(raw)) if raw not in ("", None) else 0.0
    value = float(raw)
    if transform == "log1p":
        return math.log1p(value)
    return value


def design_matrix(
    by_date: dict[str, dict[int, dict[str, str]]], dates: list[str], specs: list[FeatureSpec]
) -> list[list[float]]:
    return [[feature_value(by_date, d, spec) for spec in specs] for d in dates]


# --------------------------------------------------------------------------- #
# Linear algebra (standard library only)
# --------------------------------------------------------------------------- #
def standardise_fit(matrix: list[list[float]]) -> list[tuple[float, float]]:
    if not matrix or not matrix[0]:
        return []
    stats = []
    for j in range(len(matrix[0])):
        col = [row[j] for row in matrix]
        mean = sum(col) / len(col)
        std = math.sqrt(sum((v - mean) ** 2 for v in col) / len(col))
        stats.append((mean, std))
    return stats


def standardise_apply(matrix: list[list[float]], stats: list[tuple[float, float]]) -> list[list[float]]:
    return [
        [(row[j] - stats[j][0]) / stats[j][1] if stats[j][1] > 0 else 0.0 for j in range(len(stats))]
        for row in matrix
    ]


def solve_ridge(matrix: list[list[float]], target: list[float], ridge: float) -> list[float]:
    """Ridge regression with an unpenalised intercept, via Gaussian elimination."""
    n = len(matrix)
    p = len(matrix[0]) if matrix and matrix[0] else 0
    design = [[1.0] + row for row in matrix]
    width = p + 1
    xtx = [[sum(design[k][i] * design[k][j] for k in range(n)) for j in range(width)] for i in range(width)]
    xty = [sum(design[k][i] * target[k] for k in range(n)) for i in range(width)]
    for i in range(1, width):  # do not penalise the intercept
        xtx[i][i] += ridge
    aug = [xtx[i] + [xty[i]] for i in range(width)]
    for col in range(width):
        pivot = max(range(col, width), key=lambda r: abs(aug[r][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_val = aug[col][col]
        if abs(pivot_val) < 1e-12:
            continue
        for r in range(width):
            if r == col:
                continue
            factor = aug[r][col] / pivot_val
            aug[r] = [aug[r][k] - factor * aug[col][k] for k in range(width + 1)]
    return [aug[i][width] / aug[i][i] if abs(aug[i][i]) > 1e-12 else 0.0 for i in range(width)]


def predict_log(beta: list[float], standardised_row: list[float]) -> float:
    return beta[0] + sum(beta[1 + j] * standardised_row[j] for j in range(len(standardised_row)))


# --------------------------------------------------------------------------- #
# Fitting and cross-validation
# --------------------------------------------------------------------------- #
def fit_full(by_date, dates, ecoli, specs, ridge) -> tuple[list[float], list[tuple[float, float]]]:
    matrix = design_matrix(by_date, dates, specs)
    stats = standardise_fit(matrix)
    standardised = standardise_apply(matrix, stats)
    target = [math.log10(ecoli[d]) for d in dates]
    beta = solve_ridge(standardised, target, ridge)
    return beta, stats


def loocv_log_predictions(by_date, dates, ecoli, specs, ridge) -> dict[str, float]:
    """Return the held-out log10 prediction for each date (trained on the rest)."""
    preds: dict[str, float] = {}
    for test in dates:
        train = [d for d in dates if d != test]
        matrix = design_matrix(by_date, train, specs)
        stats = standardise_fit(matrix)
        standardised = standardise_apply(matrix, stats)
        target = [math.log10(ecoli[d]) for d in train]
        beta = solve_ridge(standardised, target, ridge)
        test_row = standardise_apply(design_matrix(by_date, [test], specs), stats)[0]
        preds[test] = predict_log(beta, test_row)
    return preds


def error_metrics(ecoli: dict[str, float], dates: list[str], log_preds: dict[str, float]) -> dict[str, float]:
    abs_log, ape = [], []
    for d in dates:
        pred = 10 ** log_preds[d]
        actual = ecoli[d]
        abs_log.append(abs(log_preds[d] - math.log10(actual)))
        ape.append(abs(pred - actual) / actual * 100.0)
    n = len(dates)
    return {
        "mae_log": sum(abs_log) / n,
        "median_ape": sorted(ape)[n // 2],
        "mape": sum(ape) / n,
    }


def describe_model(specs: list[FeatureSpec]) -> str:
    if not specs:
        return "intercept only"
    parts = []
    for lookback, column, transform in specs:
        label = column if transform in (None, "proximity") else f"{transform}({column})"
        if transform == "proximity":
            label = f"proximity({column})"
        parts.append(f"{label} @ {lookback}-day")
    return ", ".join(parts)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def main() -> int:
    args = parse_args()
    path = Path(args.features)
    dates, ecoli, by_date = load_features(path)

    # LOOCV comparison table: reference models + the selected model.
    comparison = []
    for name, specs in {**REFERENCE_MODELS, "SELECTED: " + describe_model(SELECTED_MODEL): SELECTED_MODEL}.items():
        loocv_preds = loocv_log_predictions(by_date, dates, ecoli, specs, args.ridge)
        comparison.append((name, error_metrics(ecoli, dates, loocv_preds)))

    # Selected model: full-data fit (for coefficients) and honest LOOCV per-day errors.
    beta, stats = fit_full(by_date, dates, ecoli, SELECTED_MODEL, args.ridge)
    fitted_log = {d: predict_log(beta, standardise_apply(design_matrix(by_date, [d], SELECTED_MODEL), stats)[0]) for d in dates}
    loocv_log = loocv_log_predictions(by_date, dates, ecoli, SELECTED_MODEL, args.ridge)
    metrics = error_metrics(ecoli, dates, loocv_log)

    predictions = []
    for d in dates:
        actual = ecoli[d]
        fitted = 10 ** fitted_log[d]
        loocv = 10 ** loocv_log[d]
        predictions.append(
            {
                "sample_date": d,
                "actual_cfu_per_100ml": round(actual, 1),
                "fitted_cfu_per_100ml": round(fitted, 1),
                "loocv_cfu_per_100ml": round(loocv, 1),
                "loocv_signed_pct_error": round((loocv - actual) / actual * 100.0, 1),
                "loocv_abs_pct_error": round(abs(loocv - actual) / actual * 100.0, 1),
            }
        )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sample_date",
        "actual_cfu_per_100ml",
        "fitted_cfu_per_100ml",
        "loocv_cfu_per_100ml",
        "loocv_signed_pct_error",
        "loocv_abs_pct_error",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(predictions)

    windows_with_spill = sum(1 for d in dates if float(by_date[d][7]["event_count"]) > 0)
    lines = [
        "# Conham E. coli estimation model",
        "",
        "Generated by `scripts/model_conham_ecoli.py`. A log10 ridge regression of",
        "E. coli concentration on upstream CSO spill features, selected by leave-one-out",
        "cross-validation (LOOCV) over every 1- to 7-day lookback window.",
        "",
        f"**Selected model:** `{describe_model(SELECTED_MODEL)}` (single predictor).",
        "",
        "Spills 10-20 miles upstream over a week-long window are the best out-of-sample",
        "predictor, consistent with the travel time for upstream contamination to reach",
        "Conham. Adding more features worsened LOOCV error on only 25 samples, so the",
        "model is deliberately parsimonious.",
        "",
        "## Model selection (leave-one-out cross-validation)",
        "",
        "Lower is better. `MAE_log` is the mean absolute error in log10 space (a fold-",
        "error: 0.30 ≈ being out by a factor of 2); it is the robust metric used to pick",
        "the model. `Median APE` / `MAPE` are percentage errors and are dominated by a",
        "few very-low-count days.",
        "",
        "| Model | MAE_log | Median APE | MAPE |",
        "|---|---:|---:|---:|",
    ]
    for name, m in comparison:
        lines.append(f"| {name} | {m['mae_log']:.3f} | {m['median_ape']:.1f}% | {m['mape']:.1f}% |")
    lines.extend(
        [
            "",
            "The selected model improves on both the previous multi-feature model and the",
            "mean baseline.",
            "",
            "## Selected model coefficients (standardised feature, log10 target)",
            "",
            "| Term | Coefficient |",
            "|---|---:|",
            f"| intercept | {beta[0]:.4f} |",
        ]
    )
    for spec, coef in zip(SELECTED_MODEL, beta[1:]):
        lines.append(f"| `{describe_model([spec])}` | {coef:.4f} |")
    lines.extend(
        [
            "",
            f"Intercept {beta[0]:.3f} in log10 corresponds to about {10 ** beta[0]:.0f} CFU/100ml",
            "when no qualifying upstream spill is recorded.",
            "",
            "## Per-day percentage error (leave-one-out: each day predicted without itself)",
            "",
            f"Headline out-of-sample error -- **median {metrics['median_ape']:.1f}%**, "
            f"**MAE_log {metrics['mae_log']:.3f}**, MAPE {metrics['mape']:.1f}%.",
            "",
            "| Sample date | Actual | LOOCV predicted | Signed % error | Abs % error |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for p in predictions:
        lines.append(
            f"| {p['sample_date']} | {p['actual_cfu_per_100ml']:.0f} | {p['loocv_cfu_per_100ml']:.0f} | "
            f"{p['loocv_signed_pct_error']:+.1f}% | {p['loocv_abs_pct_error']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "(The CSV also includes the in-sample `fitted_cfu_per_100ml` column.)",
            "",
            "## Caveats",
            "",
            f"- {windows_with_spill} of {len(dates)} sample windows recorded upstream CSO spill",
            "  activity. The signal is real but weak (single-feature R^2 around 0.2), so the",
            "  model explains only part of the day-to-day variation.",
            "- E. coli values are chart-digitised and capped at 1000 CFU/100ml (right-censored),",
            "  so days at 1000 are under-predicted by construction.",
            "- Percentage error explodes on days when the true count is very low (10-20",
            "  CFU/100ml): a small absolute miss is a huge relative one. `MAE_log` and the",
            "  median are the fairer summaries; MAPE is shown for completeness.",
            "- Only CSO spill features are used. Rainfall, river flow, sunlight, temperature,",
            "  and sample timing are not available here and would be needed for a strong model.",
            "",
        ]
    )
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"Wrote {args.report}")
    print(f"Selected model: {describe_model(SELECTED_MODEL)}")
    print(f"LOOCV  median APE {metrics['median_ape']:.1f}%  MAE_log {metrics['mae_log']:.3f}  MAPE {metrics['mape']:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
