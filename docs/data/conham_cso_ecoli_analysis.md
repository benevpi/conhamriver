# Conham CSO / E. coli exploratory analysis

This report is generated from `scripts/analyze_conham_cso_ecoli.py` using the Wessex Water 2025 Event Duration Monitoring ArcGIS dataset and the Conham E. coli sampling CSV.

Sample dates analysed: 25

## Best one-variable log-linear associations

| Rank | Lookback days | Feature | n | Pearson r | R^2 | Spearman rho |
|---:|---:|---|---:|---:|---:|---:|
| 1 | 7 | `spill_hours_10_to_20_miles` | 25 | 0.440 | 0.194 | 0.392 |
| 2 | 6 | `spill_hours_10_to_20_miles` | 25 | 0.406 | 0.165 | 0.301 |
| 3 | 2 | `spill_hours_10_to_20_miles` | 25 | 0.383 | 0.147 | 0.291 |
| 4 | 5 | `spill_hours_10_to_20_miles` | 25 | 0.377 | 0.142 | 0.261 |
| 5 | 3 | `spill_hours_10_to_20_miles` | 25 | 0.372 | 0.138 | 0.237 |
| 6 | 4 | `spill_hours_10_to_20_miles` | 25 | 0.365 | 0.133 | 0.222 |
| 7 | 4 | `spill_hours_1_to_5_miles` | 25 | 0.356 | 0.126 | 0.304 |
| 8 | 3 | `spill_hours_1_to_5_miles` | 25 | 0.353 | 0.125 | 0.304 |
| 9 | 1 | `spill_hours_10_to_20_miles` | 25 | 0.349 | 0.122 | 0.316 |
| 10 | 7 | `spill_hours_5_to_10_miles` | 25 | 0.334 | 0.112 | 0.358 |

## Interpretation cautions

- The E. coli values are chart-digitised approximations and capped values at 1000 CFU/100ml are right-censored.
- The CSO query uses Wessex Water's static 2025 Event Duration Monitoring dataset, bounded to the selected 1- to 7-day lookback window before each sample.
- These are simple exploratory correlations, not causal models. Rainfall, river flow, sunlight, temperature, sample time, and travel time are not controlled here.
