# Conham E. coli: per-day estimates by method

Every sample day with the out-of-sample (leave-one-out) estimate from each
model. All estimates are CFU/100ml; `e%` is the absolute percentage error.

| Date | Actual | Distance-band CSO (weighted) | e% | Individual-outfall CSO | e% | CSO + rainfall | e% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2025-05-22 | 10 | 340 | 3298% | 162 | 1515% | 255 | 2452% |
| 2025-06-17 | 140 | 344 | 146% | 135 | 4% | 166 | 18% |
| 2025-07-09 | 85 | 283 | 233% | 140 | 64% | 112 | 32% |
| 2025-07-21 | 95 | 342 | 260% | 226 | 138% | 179 | 88% |
| 2025-07-26 | 620 | 288 | 54% | 119 | 81% | 141 | 77% |
| 2025-08-02 | 85 | 395 | 365% | 85 | 0% | 210 | 148% |
| 2025-08-09 | 490 | 246 | 50% | 124 | 75% | 102 | 79% |
| 2025-08-16 | 145 | 260 | 80% | 135 | 7% | 113 | 22% |
| 2025-08-23 | 20 | 266 | 1230% | 154 | 670% | 155 | 676% |
| 2025-08-30 | 20 | 326 | 1528% | 61 | 206% | 191 | 856% |
| 2025-09-06 | 470 | 355 | 24% | 537 | 14% | 138 | 71% |
| 2025-09-13 | 1000 | 388 | 61% | 822 | 18% | 213 | 79% |
| 2025-09-20 | 100 | 282 | 182% | 67 | 33% | 104 | 4% |
| 2025-09-27 | 1000 | 198 | 80% | 118 | 88% | 84 | 92% |
| 2025-10-04 | 240 | 314 | 31% | 130 | 46% | 156 | 35% |
| 2025-10-11 | 110 | 263 | 139% | 137 | 25% | 118 | 7% |
| 2025-10-18 | 220 | 254 | 16% | 131 | 40% | 110 | 50% |
| 2025-10-25 | 180 | 407 | 126% | 188 | 5% | 215 | 19% |
| 2025-11-01 | 330 | 350 | 6% | 342 | 4% | 170 | 48% |
| 2025-11-08 | 370 | 349 | 6% | 126 | 66% | 182 | 51% |
| 2025-11-22 | 1000 | 332 | 67% | 118 | 88% | 188 | 81% |
| 2025-11-29 | 10 | 293 | 2827% | 162 | 1515% | 158 | 1478% |
| 2025-12-04 | 450 | 782 | 74% | 666 | 48% | 672 | 49% |
| 2025-12-11 | 1000 | 914 | 9% | 858 | 14% | 1019 | 2% |
| 2025-12-18 | 1000 | 911 | 9% | 958 | 4% | 991 | 1% |

## Summary error (leave-one-out)

| Model | Median abs % error | Mean abs % error |
|---|---:|---:|
| Distance-band CSO (weighted) | 79.5% | 436.0% |
| Individual-outfall CSO | 45.8% | 190.7% |
| CSO + rainfall | 50.8% | 260.6% |

Notes: percentage error is dominated by a few very-low-count days (true value
10-20 CFU/100ml), where any miss is a huge relative error -- the median is the
fairer summary. The individual-outfall model is best overall; the CSO+rainfall
model edges the others on the rain-driven December peaks but rainfall adds no
predictive power beyond the CSO signal on cross-validation.
