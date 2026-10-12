# 0007 — Extraction model: gpt-oss-120b, pinned to Venice with fallback

Date: 2026-10-11 · Status: accepted

## Context

Pain extraction runs one LLM call per post. Production used `google/gemini-2.5-flash-lite` at a 2,048-token
output limit. Experiments 0001 (11 models) and 0005 (8 more, all cheaper than gpt-oss-120b, plus provider
pinning) scored models on the same 300 real threads against a Claude Sonnet reference.

Findings: flash-lite misses pain more often (post F1 0.71–0.73) and loses ~6% of posts when its output is
cut off at the token limit — the parser then returned nothing and the post was marked processed with zero
pains. `openai/gpt-oss-120b` is at least as good on every measure (post F1 0.82–0.84, cluster agreement
within noise, ~0.3% invalid). No cheaper model matched it. The same model served by Venice scored the same
at 41% lower cost (int4 quantisation, slower per call).

## Decision

- Extraction model `openai/gpt-oss-120b` (set in `system_settings.OPENROUTER_MODEL`, not in code).
- Provider routing `OPENROUTER_PROVIDER_ORDER=venice`, `OPENROUTER_ALLOW_FALLBACKS=true`: Venice first,
  OpenRouter's default routing if Venice cannot serve.
- Unusable output is an error, never "no pains": cut-off responses are retried with double the token
  budget (up to 8,192); failures are recorded in `pain_extraction_attempts`, retried after 6 hours, and
  parked after 3 attempts.

## Consequences

- Expected cost ≈ $0.00006 per post: about $17/month to extract every new post (~9,500/day), versus
  ~$40/month for half of them before. The 1.39M-post backlog would cost ≈ $84.
- Calls take ~3.6 s on Venice versus ~1.1 s; at 20 parallel workers a 10,000-post day takes about 30 minutes.
- Pain counts per post drop (flash-lite over-extracted: 605 pains vs the reference's 369 on the sample),
  so cluster sizes in the first months after the switch are not directly comparable with earlier runs.
- Quality under real daily volume is unverified; check the first week's extractions.

## Alternatives rejected

- **Keep flash-lite** — worse and 3–4× the price.
- **glm-4.7-flash** — best cluster agreement in 0001 (0.55) but about today's price, 3–4× slower, 5% invalid.
- **granite-4.0-h-micro / mistral-nemo** — a third or less of the price, clearly worse at detecting pain and
  tools.
- **Pin Venice without fallback** — extraction would stop whenever Venice is down.
