"""Run one OpenRouter model over the frozen sample with the production prompt.

Only the model changes: same system prompt, same user prompt, temperature 0,
max_tokens 2048 (production values). Reasoning-capable models may be given a
larger max_tokens / low reasoning effort via flags; that deviation is recorded
in every output row.

Resumable: rows already present in data/outputs/<model>.jsonl are skipped.
Budget: refuses to start (or continue) if the projected total spend across all
models would exceed common.BUDGET_USD.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from common import BUDGET_USD, OUTPUTS, SAMPLE_PATH, openrouter_key, output_path, read_jsonl, total_spent
from prod_code import (PROD_MAX_TOKENS, PROD_TEMPERATURE, SYSTEM_PROMPT, pain_to_dict,
                       parse_with_status, pwc_from_sample)

URL = "https://openrouter.ai/api/v1/chat/completions"
TRANSIENT = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}


def call(client: httpx.Client, key: str, model: str, row: dict, max_tokens: int,
         reasoning_effort: str | None, retries: int = 4, provider: str | None = None) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": row["user_prompt"]}],
        "temperature": PROD_TEMPERATURE,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort}
    if provider:
        body["provider"] = {"order": [provider], "allow_fallbacks": False}
    last_error = ""
    for attempt in range(1, retries + 1):
        t0 = time.monotonic()
        try:
            resp = client.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=180)
            latency = time.monotonic() - t0
            if resp.status_code in TRANSIENT:
                last_error = f"http {resp.status_code}: {resp.text[:200]}"
                time.sleep(2 ** attempt + random.random())
                continue
            data = resp.json()
            if resp.status_code != 200 or "error" in data and not data.get("choices"):
                err = data.get("error", {}) if isinstance(data, dict) else {}
                code = err.get("code") if isinstance(err, dict) else None
                last_error = f"http {resp.status_code} code={code}: {str(err)[:200]}"
                if code in TRANSIENT and attempt < retries:
                    time.sleep(2 ** attempt + random.random())
                    continue
                return {"status": "api_error", "error": last_error, "latency_s": latency, "attempts": attempt}
            usage = data.get("usage") or {}
            choices = data.get("choices") or []
            out = {
                "latency_s": round(latency, 3),
                "attempts": attempt,
                "provider": data.get("provider"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cost_usd": float(usage.get("cost") or 0.0),
            }
            if not choices:
                # production raises ExtractionError immediately, no retry
                out.update(status="empty_choices", raw="")
                return out
            msg = choices[0].get("message") or {}
            out.update(status="ok", raw=msg.get("content") or "",
                       finish_reason=choices[0].get("finish_reason"))
            return out
        except (httpx.TransportError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2 ** attempt + random.random())
    return {"status": "transport_error", "error": last_error, "attempts": retries}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--tag", default="", help="run label, e.g. 'b' for a repeat run")
    ap.add_argument("--limit", type=int, default=None, help="first N sample rows (pilot)")
    ap.add_argument("--keys-from", default=None, help="only keys present in this outputs file (e.g. reference)")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=PROD_MAX_TOKENS)
    ap.add_argument("--reasoning-effort", default=None, choices=[None, "minimal", "low", "medium", "high"])
    ap.add_argument("--budget", type=float, default=BUDGET_USD)
    ap.add_argument("--provider", default=None, help="pin one OpenRouter provider (no fallbacks)")
    args = ap.parse_args()

    sample = [json.loads(l) for l in SAMPLE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        sample = sample[: args.limit]
    path = output_path(args.model, args.tag)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    done_rows = read_jsonl(path)
    done = {r["key"] for r in done_rows}
    todo = [r for r in sample if r["key"] not in done]

    spent = total_spent()
    model_rows = [r for r in done_rows if r.get("cost_usd")]
    if model_rows:
        per_post = sum(r["cost_usd"] for r in model_rows) / len(model_rows)
        projected = spent + per_post * len(todo)
        print(f"spent so far ${spent:.4f}; this model ${per_post:.6f}/post; "
              f"projected total after run ${projected:.4f}")
        if projected > args.budget:
            raise SystemExit(f"ABORT: projected ${projected:.2f} exceeds budget ${args.budget:.2f}")
    elif spent > args.budget:
        raise SystemExit(f"ABORT: already spent ${spent:.2f}")
    print(f"{args.model} [{args.tag or '-'}]: {len(todo)} to run, {len(done)} cached")
    if not todo:
        return

    key = openrouter_key()
    run_cost = 0.0
    counts: dict[str, int] = {}
    with httpx.Client(http2=False) as client, path.open("a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(call, client, key, args.model, r, args.max_tokens, args.reasoning_effort,
                               provider=args.provider): r
                   for r in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            row = futures[fut]
            res = fut.result()
            pains, parse_status = [], None
            if res.get("status") == "ok":
                pains_obj, parse_status = parse_with_status(res["raw"], pwc_from_sample(row))
                pains = [pain_to_dict(p) for p in pains_obj]
            res.update(key=row["key"], model=args.model, tag=args.tag, parse_status=parse_status,
                       pains=pains, max_tokens=args.max_tokens, reasoning_effort=args.reasoning_effort,
                       pinned_provider=args.provider)
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
            fh.flush()
            run_cost += res.get("cost_usd") or 0.0
            status = res["status"] if res["status"] != "ok" else f"ok/{parse_status}"
            counts[status] = counts.get(status, 0) + 1
            if spent + run_cost > args.budget:
                print(f"ABORT mid-run: budget reached (${spent + run_cost:.2f})")
                pool.shutdown(cancel_futures=True)
                break
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}  ${run_cost:.4f}  {counts}")
    print(f"done: {counts}  run cost ${run_cost:.4f}  total spent ${spent + run_cost:.4f}")


if __name__ == "__main__":
    main()
