# 0001 — Cheaper LLM for pain extraction

Date: 2026-10-10 · Code: `experiments/0001_extraction_model_eval/`

## Question

Which is the cheapest OpenRouter model that extracts pain signals at least as well as the current
production model (`google/gemini-2.5-flash-lite`), and how much would switching save?

## Setup

- **Data**: 300 threads frozen from production (read-only): 280 Reddit posts created in June 2026 from
  197 subreddits (round-robin stratified, max ~2 per subreddit) plus 20 Hacker News threads; all were
  extracted by production on/after 2026-07-01 (after the model setting was last changed, 2026-06-24).
  Production had stored pains for 127/300 threads. Mean prompt ≈ 1,080 tokens (system + user).
- **Prompt**: production `prompt_version` `v1`. `_SYSTEM_PROMPT`, `build_prompt`,
  `_build_generic_source_prompt`, `parse_pain_json` and the Reddit / Hacker News bundle converters are
  executed verbatim from `src/` (loaded with `ast`, not copied). Temperature 0, `max_tokens` 2048 as in
  `extract_for_post`.
- **Deviations**: reasoning-capable models were run with `reasoning.effort = low`. The reference ran
  with `max_tokens` 4096. qwen3.7-flash and ling-3.0-flash were additionally run with `max_tokens` 6000
  (`[mt6k]`) because reasoning tokens exhausted the 2048 budget.
- **Reference**: `anthropic/claude-sonnet-5.5` (no extended thinking). Its self-consistency was measured
  on a 50-thread repeat run (`[b]`).
- **Prices** (USD per million tokens, fetched from `openrouter.ai/api/v1/models` on 2026-10-10;
  input / output): gemini-2.5-flash-lite 0.10 / 0.40 · gpt-oss-120b 0.037 / 0.17 · gpt-oss-20b
  0.018 / 0.09 · mistral-nemo 0.029 / 0.030 · glm-4.7-flash 0.061 / 0.40 · qwen3.7-flash 0.03 / 0.13 ·
  ling-3.0-flash 0.021 / 0.063 · gemma-3-12b-it 0.05 / 0.15 · mistral-small-24b 0.05 / 0.08 ·
  gpt-5-nano 0.05 / 0.40 · claude-sonnet-5.5 2.00 / 10.00. Costs in the table are the **actual** costs
  OpenRouter reported per call (usage accounting), not list-price estimates.
- **Downstream**: pain statements embedded with the production embedder (all-MiniLM-L6-v2, normalized)
  and compared against the 672 centroids of analysis run 16 (the last healthy run; run 17 is degenerate).
- Total spend: **USD 2.96**.

## Method

Per thread, a failed call (API error, empty choices, truncated or unparsable JSON) counts as "no pains",
because that is what production does: the thread is marked processed with zero pains.

- **post F1**: does the thread contain any pain — model vs reference.
- **stmt R/P @0.7**: one-to-one greedy matching of pain statements by cosine; share of reference
  (recall) and model (precision) statements matched at ≥ 0.70.
- **nearest-cluster F1** (primary): set of (thread, nearest run-16 centroid) pairs, model vs reference.
  This is threshold-free and approximates what the monthly re-cluster sees.
- **cluster F1 @0.70**: same, but only pains the daily job would assign (cosine ≥ 0.70). Only 13 % of
  reference pains clear 0.70, so this column rests on few pairs and is noisy.
- **topic cos**: cosine between the model's and the reference's nearest-centroid histograms over the
  whole sample.
- **workaround agree / tools J**: on statement pairs matched at ≥ 0.80.
- 95 % CIs: bootstrap over threads (300 resamples).
- Projections: `$/post` × 150,000 (5,000 posts/day × 30) and × 1,390,000 (the unprocessed backlog).

## Results

| Model | valid | trunc. | pains | post F1 [95% CI] | stmt R/P @0.7 | nearest-cluster F1 [95% CI] | topic cos | cluster F1 @0.70 | workaround agree | tools J | $/post | $/month (5k/day) | $ backlog (1.39M) | p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `claude-sonnet-5.5` (reference) | 100% | 0% | 369 | – | – | – | – | – | – | – | 0.00662 | 993 | 9,207 | 1.6 |
| `claude-sonnet-5.5` repeat (50 threads; **ceiling**) | 100% | 0% | 59 | 0.97 [0.89–1.00] | 78% / 83% | 0.66 [0.54–0.79] | 0.84 | 0.67 | 93% | 0.74 | 0.00687 | – | – | 1.6 |
| `z-ai/glm-4.7-flash` | 95% | 4% | 495 | 0.82 [0.76–0.88] | 59% / 44% | **0.55** [0.49–0.61] | 0.74 | 0.47 | 90% | 0.71 | 0.000248 | 37.14 | 344 | 3.5 |
| `qwen/qwen3.7-flash` [mt6k] | 100% | 0% | 260 | 0.81 [0.74–0.87] | 25% / 36% | 0.42 [0.37–0.47] | 0.62 | 0.36 | 80% | 0.79 | 0.000227 | 34.09 | 316 | 8.3 |
| `openai/gpt-oss-20b` | 100% | 0% | 386 | 0.80 [0.74–0.85] | 25% / 24% | 0.41 [0.35–0.47] | 0.65 | 0.30 | 89% | 0.49 | 0.000079 | 11.92 | 110 | 1.9 |
| **`openai/gpt-oss-120b`** | 100% | 0% | 298 | **0.83** [0.77–0.89] | 30% / 38% | 0.41 [0.35–0.46] | 0.65 | 0.30 | 84% | 0.70 | **0.000092** | **13.83** | **128** | 1.1 |
| `google/gemini-2.5-flash-lite` (**current**) | 94% | 6% | 605 | 0.73 [0.66–0.79] | 41% / 25% | 0.38 [0.33–0.43] | 0.63 | 0.36 | 84% | 0.68 | 0.000269 | 40.39 | 374 | 0.9 |
| `gemini-2.5-flash-lite` repeat | 94% | 6% | 605 | 0.73 | 41% / 25% | 0.38 | 0.63 | 0.36 | 85% | 0.68 | 0.000260 | 39.05 | 362 | 1.1 |
| production output (stored) | – | – | 601 | 0.73 [0.66–0.79] | 41% / 25% | 0.38 [0.33–0.42] | 0.64 | 0.38 | 84% | 0.72 | – | – | – | – |
| `mistralai/mistral-small-24b-instruct-2501` | 94% | 5% | 469 | 0.69 [0.63–0.76] | 29% / 23% | 0.36 [0.30–0.41] | 0.60 | 0.39 | 84% | 0.65 | 0.000078 | 11.74 | 109 | 2.4 |
| `inclusionai/ling-3.0-flash` [mt6k] | 92% | 8% | 451 | 0.65 [0.58–0.73] | 32% / 26% | 0.34 [0.28–0.40] | 0.57 | 0.38 | 79% | 0.76 | 0.000102 | 15.27 | 141 | 1.9 |
| `openai/gpt-5-nano` | 97% | 3% | 580 | 0.66 [0.59–0.71] | 24% / 16% | 0.32 [0.26–0.37] | 0.56 | 0.30 | 68% | 0.47 | 0.000362 | 54.35 | 504 | 5.0 |
| `mistralai/mistral-nemo` | 98% | 0% | 410 | 0.77 [0.70–0.82] | 17% / 15% | 0.31 [0.25–0.36] | 0.56 | 0.36 | 75% | 0.66 | 0.000035 | 5.26 | 49 | 1.0 |
| `google/gemma-3-12b-it` | 59% | 0% | 503 | 0.69 [0.61–0.76] | 26% / 19% | 0.30 [0.26–0.35] | 0.57 | 0.26 | 84% | 0.59 | 0.000091 | 13.67 | 127 | 0.4 |
| `inclusionai/ling-3.0-flash` (2048) | 80% | 20% | 230 | 0.49 [0.39–0.58] | 19% / 30% | 0.22 [0.16–0.28] | 0.46 | 0.29 | 86% | 0.68 | 0.000068 | 10.25 | 95 | 1.6 |
| `qwen/qwen3.7-flash` (2048) | 76% | 24% | 6 | 0.08 [0.02–0.16] | 1% / 50% | 0.02 [0.00–0.05] | 0.09 | 0.00 | 100% | 0.75 | 0.000178 | 26.73 | 248 | 8.2 |

Observations:

- **gpt-oss-120b** is at least as good as flash-lite on every quality column (post F1 0.83 vs 0.73,
  CIs barely overlapping; nearest-cluster F1 0.41 vs 0.38, CIs overlap; tools J 0.70 vs 0.68), has
  essentially no failures (1/300 truncation vs 17/300), and costs **66 % less** per thread
  (USD 13.8 vs 40.4 per month at 5,000 posts/day; USD 128 vs 374 for the backlog).
- **glm-4.7-flash** agrees best with the reference (nearest-cluster F1 0.55, the only CI above
  flash-lite's), at roughly flash-lite's cost — the choice if quality, not price, is the goal. Its 4 %
  truncations disappear with a larger `max_tokens`; it is 3–4× slower.
- **gpt-oss-20b** matches gpt-oss-120b on detection and clustering at 14 % lower cost, but its tool
  extraction is clearly weaker (tools J 0.49).
- **mistral-nemo** is the cheapest (−87 %) but loses on the downstream metric (0.31) and on the
  workaround flag (75 %).
- **flash-lite is deterministic** (repeat run identical) and the stored production output reproduces a
  fresh run (nearest-cluster F1 0.96 vs fresh run), so the production numbers above are representative.
- **flash-lite over-extracts** relative to the reference (605 vs 369 pains) — high statement recall,
  low precision — and loses **6 % of threads to `max_tokens` truncation**: the JSON is cut mid-array,
  `parse_pain_json` returns `[]`, and production marks the thread processed with zero pains.
- Disqualified as configured: gemma-3-12b (41 % empty or preamble responses — providers mishandle the
  system prompt), qwen3.7-flash and ling-3.0-flash at 2048 tokens (reasoning consumes the budget),
  gpt-5-nano (more expensive than flash-lite once reasoning tokens are billed).

Production quirks found while reusing the code (not fixed here; for the restructure):

1. `max_tokens=2048` silently drops all pains on long, pain-rich threads (6 % with flash-lite).
2. `parse_pain_json`'s backslash-apostrophe repair is a no-op: `fixed.replace("\'", "'")` — in Python
   `"\'"` is just `'`.
3. The parser strips only a leading code fence; any prose preamble ("Here are the pain signals…") makes
   the whole response unparsable (mistral-nemo, gemma).
4. "Empty choices" raises immediately without retry in `extract_for_post`.

## What this does not establish

- **The reference is not ground truth.** Claude Sonnet 5.5 agrees with itself at only 0.66
  nearest-cluster F1 on a 50-thread repeat; "pain signal" is a judgement call, and differences between
  candidates below ~0.1 are within noise. The 50-thread hand-check sheet (`data/handcheck.md`,
  reference vs gpt-oss-120b vs glm-4.7-flash vs stored production) has not yet been reviewed by a human.
- **One prompt.** All models ran the v1 prompt tuned (implicitly) for flash-lite; another prompt could
  reorder the ranking.
- **One month of data.** June 2026 threads, mostly Reddit; Hacker News is only 20 threads.
- **Provider variance.** OpenRouter routed gpt-oss-120b across 17 providers (and gpt-oss-20b across 9);
  output and price can differ by provider and quantization. Pinning a provider in production may change
  both.
- **Downstream is approximated.** The comparison uses run-16 centroids (a different 90-day window) and
  the nearest centroid; it does not re-run HDBSCAN on a full month extracted by each model. A full
  re-cluster comparison would need tens of thousands of threads per model.
- **Cost projections** assume this sample's token mix (≈1,080 input tokens per thread). Backlog threads
  (Jan/Feb/Jun 2026) may differ.
