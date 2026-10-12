# Experiment 0001 — extraction model eval

Compares OpenRouter models on the **production** pain-extraction prompt and parser. Write-up:
`docs/experiments/0001-extraction-model-eval.md`.

The prompt builders and `parse_pain_json` are loaded verbatim from `src/pain_extraction/` with `ast`
(`prod_code.py`), so only the model changes. Production call settings: temperature 0, `max_tokens` 2048.

## Prerequisites

- `~/.reddit_signal/readonly.env` with `RS_RO_DATABASE_URL` (read-only role), and an SSH tunnel:
  `ssh -N -L 15433:127.0.0.1:5433 root@<server>`
- The OpenRouter key is read from `system_settings` at runtime; it is never written to disk.
- `uv` (Python 3.11 is pinned in `pyproject.toml`).

## Rerun

```bash
cd experiments/0001_extraction_model_eval
uv sync --python 3.11
uv run python sample.py                      # freezes 280 Reddit + 20 HN threads to data/sample.jsonl
uv run python run_model.py anthropic/claude-sonnet-5.5 --max-tokens 4096          # reference
uv run python run_model.py google/gemini-2.5-flash-lite                            # baseline
uv run python run_model.py openai/gpt-oss-120b --reasoning-effort low --limit 20  # pilot, then without --limit
uv run python evaluate.py --handcheck-models openai/gpt-oss-120b z-ai/glm-4.7-flash
```

`run_model.py` is resumable (cached rows are skipped) and refuses to start when the projected total spend
across all runs would exceed `common.BUDGET_USD` (8 USD).

Outputs (all under `data/`, gitignored because they contain Reddit content): `sample.jsonl`,
`outputs/<model>.jsonl`, `results.md`, `results.json`, `handcheck.md`.
