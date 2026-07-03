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

## Daily CSO series (every day, not just sample dates)

`scripts/daily_cso.py` produces a **continuous daily** CSO spill record so the
2025 graph can show upstream spilling every day, not only on the 25 E. coli
sample dates. It queries the same Wessex Water EDM 2025 ArcGIS view and Conham
watercourse filter as `analyze_conham_cso_ecoli.py`, over the whole year in one
pass, and aggregates spill duration into a daily calendar:

```bash
python scripts/daily_cso.py fetch    # ArcGIS -> conham_cso_events_2025.csv + conham_cso_daily.csv
python scripts/daily_cso.py build     # offline: re-aggregate the daily CSV from committed raw events
```

`fetch` needs `services.arcgis.com` egress; run it where that is allowed and
commit both `conham_cso_events_2025.csv` (raw per-event dump) and
`conham_cso_daily.csv` (the daily aggregate). Columns of the daily CSV:
`date`, `spill_hours_day` (hours from events starting that day),
`spill_hours_2d` / `spill_hours_7d` (trailing 2- and 7-day cumulative sums,
ending on that day inclusive) and `event_count_day`. `build_2025_timeseries.py`
uses this file when present to populate the CSO panels every day, falling back to
the sample-only feature windows otherwise.

## Other rivers: EA bathing-water data + page builder

To reproduce the bacteria/classification view for another river, two scripts pull
Environment Agency data and render a standalone page:

```bash
python scripts/ea_bathing_water.py fetch --eubwid ukl1602-36700 \
    --out docs/data/ilkley_sampling.csv --years 2021-2025      # EA API -> sampling CSV
python scripts/build_river_page.py --samples docs/data/ilkley_sampling.csv \
    --title "River Wharfe at Ilkley" --out docs/ilkley.html    # offline -> standalone page
```

`ea_bathing_water.py fetch` needs `environment.data.gov.uk` egress; find a site's
`eubwid` in its Swimfo profile URL (`.../profile.html?site=<eubwid>`) and verify
it before trusting the output (use `--debug` to dump a raw sample if the API's
determinand field names differ). It writes E. coli + intestinal enterococci in
the same shape as `conham_sampling_2025_2026.csv`. `build_river_page.py` then
computes the bathing-water classification (shared logic from
`build_2025_timeseries.py`) and renders both indicators on one panel with the
class as vertical time bands — the portable version of the Conham bacteria panel.
It works on any sampling CSV in that format.

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

## Rainfall intensity across the catchment

`scripts/rainfall_intensity.py` looks at rainfall *intensity* (the heaviest
single hour in a day, mm/hour) rather than daily totals, at ~30 sites spread
across the Bristol Avon catchment (Bristol, Bath, the Chew and Frome
sub-catchments and the upper-Avon headwaters). The point is to catch localised
convective **thunderstorms** — a cell can dump heavy rain on one tributary while
the rest of the catchment stays dry, which a single Conham point and daily totals
both miss. It pulls **hourly** precipitation (from the Open-Meteo ERA5 reanalysis
archive) plus **CAPE** (Convective Available Potential Energy, a
thunderstorm-likelihood proxy) and best-effort **lightning potential** (LPI) —
the last two from the Open-Meteo Historical Forecast API, because the reanalysis
archive carries neither. A heavy rain hour on a high-CAPE day is likely a
convective storm cell:

```bash
python scripts/rainfall_intensity.py sites     # list the catchment sites (no network)
python scripts/rainfall_intensity.py fetch     # Open-Meteo hourly -> the two CSVs below
```

`fetch` needs outbound access to `archive-api.open-meteo.com` and
`historical-forecast-api.open-meteo.com`; run it where that is allowed and commit
both outputs:

- `rainfall_intensity_by_site.csv` — tidy long form: `date, site, lat, lon,
  rain_total_mm, rain_max_mm_per_h, peak_hour, cape_max_j_per_kg,
  cape_at_peak_hour_j_per_kg, lightning_potential_max`;
- `rainfall_intensity_daily_max.csv` — wide: one row per day, one column per site
  of the peak hourly intensity, plus catchment-wide summaries of the worst
  downpour (`catchment_max_mm_per_h` / `catchment_max_site`), the highest CAPE
  (`catchment_max_cape_j_per_kg` / `catchment_max_cape_site`) and the highest
  lightning potential (`catchment_max_lightning_potential` /
  `catchment_max_lightning_site`), each with the site it occurred at.

Note: `lightning_potential` is best-effort — only some forecast models produce it
and UK coverage isn't guaranteed, so those columns may come back blank (the fetch
prints a note if so). CAPE is the reliable convective signal.

By default `fetch` covers the E. coli sampling window (first sample minus a
buffer .. last sample); override with `--start`/`--end`. Caveat: ERA5 is a
~9-11 km reanalysis grid, not a rain gauge, so it smooths the sharpest convective
peaks — treat the intensity as a lower bound and a relative (site-to-site,
day-to-day) signal, not an absolute gauge reading.

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
