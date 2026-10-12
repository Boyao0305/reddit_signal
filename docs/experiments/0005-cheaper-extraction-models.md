# 0005 — Extraction models cheaper than gpt-oss-120b

Date: 2026-10-11 · Code: `experiments/0001_extraction_model_eval/` (harness of 0001; `--provider` flag added)

## Question

Experiment 0001 recommended `openai/gpt-oss-120b` (USD 0.000092 per post). Is there a model that costs
less per post and extracts at least as well?

## Setup

- **Data**: the 0001 sample regenerated with `sample.py` defaults (month 2026-06, processed since
  2026-07-01, 280 Reddit + 20 HN, seed 1). The 0001 outputs were lost, so identity cannot be proven by key
  list; the fingerprint matches (280 Reddit threads from 197 subreddits, 127/300 with stored production
  pains, mean prompt 4,074 chars) and flash-lite again fails on exactly 17/300 threads.
- **Prompt / parser / settings**: production v1 prompt and `parse_pain_json`, loaded verbatim from
  `src/reddit_signal/pain_extraction/` (unchanged since 0001 except the package move). Temperature 0,
  `max_tokens` 2048. Reasoning-capable models at `reasoning.effort = low`.
- **Reference**: `anthropic/claude-sonnet-5.5`, `max_tokens` 4096, re-run (a new run, so absolute scores
  differ slightly from 0001; comparisons are within this table). No repeat run for self-agreement this time
  (account credit); 0001 measured the reference's ceiling at nearest-cluster F1 0.66 [0.54–0.79].
- **Comparators re-run**: `google/gemini-2.5-flash-lite` (production) and `openai/gpt-oss-120b` (default
  routing).
- **Candidates** (list prices USD/M tokens in/out from `openrouter.ai/api/v1/models`, 2026-10-11):
  ibm-granite/granite-4.0-h-micro 0.017/0.112 · meta-llama/llama-3.1-8b-instruct 0.05/0.08 ·
  amazon/nova-micro-v1 0.035/0.14 · cohere/command-r7b-12-2024 0.037/0.15 · inception/mercury-2.5 0.04/0.15 ·
  nex-agi/nex-n2.5-mini 0.025/0.10 · inference-net/schematron-v2-turbo 0.03/0.15 · google/gemma-3-4b-it
  0.05/0.10 · **gpt-oss-120b pinned to provider Venice** (int4, 0.03/0.15; cheapest of its 17 providers;
  pinned with `provider.order=["Venice"], allow_fallbacks=false`).
- Costs below are actual OpenRouter-reported costs. Total spend USD 2.27.

## Method

As in 0001: 20-post pilot per candidate, then 300 posts; same metrics (post F1, statement recall/precision,
nearest-cluster F1 against run 16's 672 centroids, tools Jaccard, workaround agreement), bootstrap 95% CIs.

Dropped after the pilot (most outputs unusable): **schematron-v2-turbo** (16/20 not a JSON array),
**gemma-3-4b-it** (12/20 JSON errors). **nex-n2.5-mini** was started on the full run but stopped: 11 valid of
32 calls (21 transport/API/JSON failures), median 4.6 s and runs of minutes per call.

## Results

| Model | valid | pains | post F1 [95% CI] | nearest-cluster F1 [95% CI] | tools J | workaround agree | $/post | $/month (5k/day) | $ backlog (1.39M) | p50 s |
|---|---|---|---|---|---|---|---|---|---|---|
| `claude-sonnet-5.5` (reference) | 100% | 375 | – | – | – | – | 0.006663 | 999 | 9,262 | 2.2 |
| **`gpt-oss-120b` @ Venice** | 100% | 318 | **0.84** [0.79–0.89] | **0.38** [0.34–0.43] | 0.74 | 88% | **0.0000602** | **9.04** | **83.7** | 3.6 |
| `gpt-oss-120b` (default routing) | 99% | 296 | 0.82 [0.76–0.87] | 0.38 [0.33–0.43] | 0.67 | 86% | 0.0001028 | 15.42 | 142.9 | 1.1 |
| `gemini-2.5-flash-lite` (production) | 94% | 605 | 0.71 [0.64–0.78] | 0.32 [0.28–0.37] | 0.68 | 86% | 0.0002693 | 40.39 | 374.3 | 1.1 |
| production output (stored) | – | 601 | 0.71 [0.64–0.78] | 0.32 [0.27–0.36] | 0.71 | 89% | – | – | – | – |
| `granite-4.0-h-micro` | 99% | 272 | 0.67 [0.60–0.73] | 0.31 [0.25–0.35] | 0.50 | 52% | **0.0000314** | 4.71 | 43.7 | 3.3 |
| `nova-micro-v1` | 100% | 558 | 0.53 [0.47–0.59] | 0.24 [0.21–0.28] | 0.52 | 83% | 0.0000684 | 10.26 | 95.1 | 1.0 |
| `llama-3.1-8b-instruct` | 90% | 643 | 0.58 [0.52–0.66] | 0.24 [0.20–0.29] | 0.56 | 87% | 0.0000424 | 6.36 | 58.9 | 2.4 |
| `command-r7b-12-2024` | 93% | 786 | 0.43 [0.36–0.49] | 0.19 [0.14–0.22] | 0.48 | 91% | 0.0001073 | 16.09 | 149.1 | 2.9 |
| `mercury-2.5` | 70% | 11 | 0.12 [0.05–0.20] | 0.01 [0.00–0.03] | – | – | 0.0002112 | 31.68 | 293.6 | 2.9 |

- **No different, cheaper model matches gpt-oss-120b.** The closest is granite-4.0-h-micro (a third of the
  cost): roughly at flash-lite's level on clusters (0.31 vs 0.32) but clearly worse on pain detection (0.67),
  tools (0.50) and workaround agreement (52%). The 8B-class models over-extract and score 0.19–0.24.
- **The same model on its cheapest provider is the cheaper option**: gpt-oss-120b via Venice scored the same
  as default routing (0.84 / 0.38 vs 0.82 / 0.38; CIs overlap fully) at **41% lower cost**, 100% valid; median
  latency 3.6 s instead of 1.1 s.
- List price misleads for reasoning models: mercury-2.5 billed USD 0.00021/post (2.3× gpt-oss-120b) and
  produced unusable output 30% of the time.

## What this does not establish

- One prompt, one month (June 2026), 300 threads; the reference is a model, not ground truth (0001: its
  self-agreement ceiling is 0.66 on nearest-cluster F1). Differences below ~0.1 there are noise.
- Venice serves an int4 quantisation; quality held on this sample, but a single provider is a single point
  of failure (no fallbacks when pinned) and its price or availability can change. Not measured: behaviour
  under sustained daily volume (10k calls/day), rate limits, or latency at concurrency 20.
- Candidates were chosen by list price below gpt-oss-120b; models that are cheaper only through specific
  providers or batch endpoints (`:batch`) were not covered, except Venice for gpt-oss-120b.
