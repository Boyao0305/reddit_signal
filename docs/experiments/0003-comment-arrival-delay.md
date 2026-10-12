# 0003 — How long after a post do its comments arrive?

Date: 2026-10-10 · Data: production `posts`/`comments`, read through `reddit_signal_ro`.

## Question

Extraction reads a post together with its comments and processes each post exactly once. With
newest-first selection, how old must a post be before its comment thread is complete enough to extract?

## Setup

The first 3,000 posts (by id) created 2026-09-01..2026-09-08, a period long since fully fetched, and their
39,998 comments. The daily fetch re-scans the last 7 days of comments every run.

## Method

For each comment, delay = `comments.created_utc - posts.created_utc`. Report the cumulative share.

## Results

| Within | 6 h | 1 day | 2 days | 3 days | 7 days |
|---|---|---|---|---|---|
| Share of comments | 61.7% | 89.3% | 95.1% | 96.7% | 99.0% |

The daily extraction window starts at **2 days** old (`DEFAULT_MATURITY_DAYS` in
`core/extraction_policy.py`), capturing about 95% of comments. Observed while testing the selector: the 200
newest unprocessed posts (all from the previous day) had zero projected comments.

## What this does not establish

- One week of posts, ordered by id; subreddits with different rhythms (weekly threads, slow communities)
  may differ.
- It measures comment *creation*, not when our fetch stored them; the 7-day comment re-scan makes the
  two close but not identical.
- It says nothing about which comments carry the pain signal; late comments may matter more or less
  than their share.
