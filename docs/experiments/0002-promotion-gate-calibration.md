# 0002 — Calibrating the monthly promotion gate

> Superseded by 0004: the members figures below include a month of post-build daily assignments.

Date: 2026-10-10 · Data: production `analysis_runs` 9–17, read through the `reddit_signal_ro` role.

## Question

What size check would have stopped run 17 (built from a near-empty 90-day window) from going live,
without blocking any normal month?

## Setup

Promoted runs and their sizes (run 11 failed and was never promoted):

| Run | Window | Clusters | Candidates | Members |
|---|---|---|---|---|
| 9 | 2020-01-01..2026-05-03 | 1,542 | 691 | 150,800 |
| 10 | 2026-02-05..2026-05-06 | 2,594 | 1,071 | 655,514 |
| 12 | 2026-03-19..2026-06-17 | 1,707 | 612 | 176,917 |
| 13 | 2026-03-26..2026-06-24 | 1,519 | 1,160 | 160,742 |
| 14 | 2026-04-02..2026-07-01 | 1,291 | 937 | 138,204 |
| 15 | 2026-05-03..2026-08-01 | 683 | 362 | 66,710 |
| 16 | 2026-06-09..2026-09-07 | 672 | 333 | 60,178 |
| 17 | 2026-07-03..2026-10-01 | 33 | 1 | 2,071 |

## Method

Replay each run against candidate rules and count which runs each rule would block.

1. *Ratio vs the previous run, 0.5×*: blocks 17, but also 12 (members 0.27× of run 10).
2. *Ratio vs the largest of the last three runs, 0.5×*: blocks 17, but also 14, 15, 16 (run 10 was
   inflated by an extraction backfill).
3. *Ratio vs the median of the last three promoted runs*, thresholds at half the smallest ratio seen in
   a normal month (clusters 0.53 → 0.25, members 0.27 → 0.13, candidates 0.39 → 0.20).

## Results

Rule 3 blocks run 17 only, on all three metrics (0.05×, 0.03×, 0.003× of the median). It also blocks a
second collapse in the following month, because the median of runs 15–17 is still 672 clusters. It is
encoded in `src/reddit_signal/core/promotion.py`; the replay is `tests/core/test_promotion.py`.

## What this does not establish

- It is a **collapse** guard. Runs 15 and 16 were already about half-sized because of the extraction
  backlog and pass it. Gradual coverage loss must be caught elsewhere (extraction coverage in
  `cli status`).
- Eight runs is a small sample; thresholds should be revisited after a few more months.
- A deliberate change (for example removing a source) can legitimately fail it; the CLI `--force`
  override exists for that and records what it overrode.
