# Conham CSO / E. coli exploratory analysis

This report is generated from `scripts/analyze_conham_cso_ecoli.py` using the Wessex Water ArcGIS query pattern from `poo.py` and the Conham E. coli sampling CSV.

Sample dates analysed: 25

## Best one-variable log-linear associations

| Rank | Lookback days | Feature | n | Pearson r | R² | Spearman ρ |
|---:|---:|---|---:|---:|---:|---:|
| 1 | 2 | `event_count` | 25 | 0.137 | 0.019 | 0.142 |
| 2 | 2 | `spill_hours_5_to_10_miles` | 25 | 0.137 | 0.019 | 0.142 |
| 3 | 2 | `spill_hours_total` | 25 | 0.137 | 0.019 | 0.142 |
| 4 | 3 | `event_count` | 25 | 0.137 | 0.019 | 0.142 |
| 5 | 3 | `spill_hours_5_to_10_miles` | 25 | 0.137 | 0.019 | 0.142 |
| 6 | 3 | `spill_hours_total` | 25 | 0.137 | 0.019 | 0.142 |
| 7 | 4 | `event_count` | 25 | 0.137 | 0.019 | 0.142 |
| 8 | 4 | `spill_hours_5_to_10_miles` | 25 | 0.137 | 0.019 | 0.142 |
| 9 | 4 | `spill_hours_total` | 25 | 0.137 | 0.019 | 0.142 |
| 10 | 5 | `event_count` | 25 | 0.137 | 0.019 | 0.142 |

## Interpretation cautions

- The E. coli values are chart-digitised approximations and capped values at 1000 CFU/100ml are right-censored.
- The ArcGIS live layer may expose only each monitor's latest event; if so, historical windows can be incomplete unless the service retains older events.
- These are simple exploratory correlations, not causal models. Rainfall, river flow, sunlight, temperature, sample time, and travel time are not controlled here.
