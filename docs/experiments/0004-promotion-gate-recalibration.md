# 0004 — Promotion gate, recalibrated on clustering-time members

Date: 2026-10-10 · Data: production `analysis_runs` 9–17 via `reddit_signal_ro`. Supersedes 0002.

## Question

Experiment 0002 measured "members" as the current size of `pain_cluster_members` per run. Daily centroid
assignment keeps adding members to the live run for a month (run 10 grew from 270,502 to 655,514), so a
just-built run is compared against grown ones. What do the ratios look like with a measure that is
available, and comparable, at promotion time?

## Setup

Clustering-time members = `analysis_runs.total_points - noise_count` (written when the run is built).

| Run | Status | Clusters | Clustering-time members | Candidates |
|---|---|---|---|---|
| 9 | historical | 1,542 | 150,800 | 691 |
| 10 | historical | 2,594 | 270,502 | 1,071 |
| 11 | failed | 2,126 | 223,826 | 0 |
| 12 | historical | 1,707 | 170,745 | 612 |
| 13 | historical | 1,519 | 157,843 | 1,160 |
| 14 | historical | 1,291 | 131,713 | 937 |
| 15 | historical | 683 | 62,649 | 362 |
| 16 | historical | 672 | 56,231 | 333 |
| 17 | live | 33 | 2,017 | 1 |

## Method

For each promoted run, the ratio of each metric to the median of the previous three promoted runs.

## Results

| Run | Clusters | Members | Candidates |
|---|---|---|---|
| 10 | 1.68 | 1.79 | 1.55 |
| 12 | 0.83 | 0.81 | 0.69 |
| 13 | 0.89 | 0.92 | 1.68 |
| 14 | 0.76 | 0.77 | 0.87 |
| 15 | 0.45 | 0.40 | 0.39 |
| 16 | 0.52 | 0.43 | 0.36 |
| 17 | 0.05 | 0.03 | 0.00 |

The smallest normal ratio is 0.36–0.45 on every metric; one threshold of **0.2×** (about half) blocks run
17 only. Encoded in `core/promotion.py` (`MIN_RATIO`); replayed in `tests/core/test_promotion.py`.

## What this does not establish

Same limits as 0002: a collapse guard, not a coverage monitor (runs 15–16 were already degraded by the
extraction backlog and pass); eight runs; deliberate changes may need `--force`.
