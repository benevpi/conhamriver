# Conham sampling graph data

These CSV files contain values digitised from the 2025-26 sampling programme graph on the Conham Bathing sampling page.

Source page: <https://www.conhambathing.co.uk/sampling>

Files:

- `conham_sampling_2025_2026.csv`: combined Escherichia coli and intestinal enterococci readings by sample date.
- `conham_sampling_2025_2026_e_coli.csv`: Escherichia coli readings only.
- `conham_sampling_2025_2026_intestinal_enterococci.csv`: intestinal enterococci readings only.

Notes:

- Values were read from the published graph image, so dates and concentrations should be treated as approximate unless the original lab spreadsheet is obtained.
- The page notes that the lab records results only up to 1000 CFU/100ml; values shown at 1000 may represent capped `>1000` readings.
- Columns in the combined CSV:
  - `sample_date`: approximate sample date inferred from the chart x-axis.
  - `e_coli_cfu_per_100ml`: Escherichia coli concentration in CFU/100ml.
  - `intestinal_enterococci_cfu_per_100ml`: intestinal enterococci concentration in CFU/100ml.
  - `value_note`: caveats for capped values.
  - `source`: source web page for the graph.

## CSO / E. coli exploratory analysis

Run `scripts/analyze_conham_cso_ecoli.py` from the repository root to query the
same Wessex Water ArcGIS layer and Conham upstream river filters used by
`poo.py`. The script builds 1- to 7-day lookback windows before each E. coli
sample date, writes CSO summary features to
`docs/data/conham_cso_ecoli_features.csv`, and writes a markdown report of simple
one-variable log-linear correlations to
`docs/data/conham_cso_ecoli_analysis.md`.

Example:

```bash
python scripts/analyze_conham_cso_ecoli.py
```

The analysis is exploratory: the sampling values are approximate/capped, the live
ArcGIS service may not retain full historical event records, and the simple
models do not control for rainfall, river flow, sunlight, temperature, sample
time, or travel time.
