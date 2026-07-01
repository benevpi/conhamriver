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
Wessex Water 2025 Event Duration Monitoring ArcGIS dataset and Conham
watercourse filters used by `poo.py`. The script builds 1- to 7-day lookback
windows before each E. coli sample date, fetches events whose `EventStart` falls
within each window, and sums the returned event durations by distance band. It
pages through capped ArcGIS result sets so multi-day windows do not silently miss
spill records, writes CSO summary features to
`docs/data/conham_cso_ecoli_features.csv`, and writes a markdown report of simple
one-variable log-linear correlations to
`docs/data/conham_cso_ecoli_analysis.md`.

Example:

```bash
python scripts/analyze_conham_cso_ecoli.py
```

The output includes `queried_feature_count` as a check for how many raw ArcGIS
features were returned before upstream filtering and de-duplication. The analysis
is exploratory: the sampling values are approximate/capped, the EDM dataset is a
published 2025 snapshot, and the simple models do not control for rainfall,
river flow, sunlight, temperature, sample time, or travel time.

## Per-outfall E. coli model

`scripts/model_conham_ecoli_by_site.py` builds a model from **individual CSO
outfalls** instead of distance bands, to see which specific outfalls track E.
coli at Conham. It uses the same Event Duration Monitoring 2025 ArcGIS view as
`analyze_conham_cso_ecoli.py`. It runs in two steps so the network query and the
modelling are independent:

```bash
python scripts/model_conham_ecoli_by_site.py fetch   # queries ArcGIS -> docs/data/conham_cso_site_features.csv
python scripts/model_conham_ecoli_by_site.py model   # offline: ranking + LOOCV model
```

`fetch` needs outbound access to `services.arcgis.com`; run it where that is
allowed and commit `conham_cso_site_features.csv`. `model` then ranks outfalls by
their univariate correlation with E. coli, forward-selects a small set by
leave-one-out cross-validation, and writes
`docs/data/conham_ecoli_site_model.md` plus
`docs/data/conham_ecoli_site_model_predictions.csv`. The band model
(`scripts/model_conham_ecoli.py` / `conham_ecoli_model.md`) is kept for
comparison.

## Weather influence

`scripts/weather_conham_ecoli.py` tests whether rainfall and temperature
influence E. coli, on their own and on top of the CSO signal. It pulls a daily
record for Conham from the Open-Meteo ERA5 historical archive (no API key):

```bash
python scripts/weather_conham_ecoli.py fetch     # Open-Meteo -> conham_weather_daily.csv + conham_upstream_weather_daily.csv
python scripts/weather_conham_ecoli.py analyze   # offline: correlations + combined CSO+weather model
```

`fetch` needs outbound access to `archive-api.open-meteo.com`; run it where that
is allowed and commit both `conham_weather_daily.csv` (Conham) and
`conham_upstream_weather_daily.csv` (Bath, ~8-9 miles upstream where the
spill-driving CSO cluster sits). `analyze` summarises local and upstream rainfall
and temperature over 1- to 7-day windows before each sample, ranks them by
correlation with E. coli, compares leave-one-out cross-validation for
rainfall-only (local and upstream) / CSO-only / combined models, and writes
`docs/data/conham_weather_ecoli_analysis.md` plus
`docs/data/conham_weather_ecoli_predictions.csv`. If the upstream CSV is absent
it falls back to Conham-only.

## Per-day model comparison

`scripts/compare_conham_models.py` reads the leave-one-out prediction CSVs from
all three models and writes a single per-day side-by-side table to
`docs/data/conham_ecoli_model_comparison.{md,csv}`. Run it after the models.

## Nearby-CSO investigation (other watercourses)

The models above only see outfalls on seven hard-coded Conham watercourses.
`scripts/investigate_nearby_csos.py` widens the net to find CSOs on *any*
watercourse that could explain the unexplained high-E. coli days:

```bash
python scripts/investigate_nearby_csos.py fetch    # ArcGIS by geography -> docs/data/conham_nearby_cso_events.csv
python scripts/investigate_nearby_csos.py report   # offline: which nearby outfalls spilled before each spike
```

`fetch` queries the EDM 2025 view by bounding box (no watercourse-name filter)
and needs `services.arcgis.com` egress; commit `conham_nearby_cso_events.csv`,
then `report` lists, for each high-E. coli day, the upstream outfalls that
spilled in the prior 7 days, flagging those on watercourses outside the existing
filter. Output: `docs/data/conham_nearby_cso_investigation.md`.

## Bristol Avon citizen water-quality data (FreshWater Watch)

`scripts/extract_bristol_avon.py` pulls the Bristol Avon rows out of the large
FreshWater Watch export `Global_Data_Set_XvsX_0.csv` (a ~73k-row global
citizen-science dataset). The dataset's management-catchment tag is unreliable
here (the tidal Avon at Conham is tagged "Severn England TraC", Kennet & Avon
canal sites as "Avon Hampshire"), so selection is by **site name + geography +
water-body type**: flowing waters (rivers/streams) on the English side near
Conham, excluding canals/locks/ponds/lakes and the North Somerset coastal
streams that drain to the Severn rather than the Avon.

```bash
python scripts/extract_bristol_avon.py
```

Writes `docs/data/bristol_avon_freshwater_watch.csv` (~2,470 river/stream rows,
2014-2026; 341 in 2025) with a parsed `watercourse` label, `distance_to_conham_mi`,
`upstream_of_conham`, nitrate/phosphate, and field observations. Note this survey
measures nutrients (nitrate/phosphate) and observations only -- it contains no
E. coli data (the bacteria columns are empty across the whole source file).
