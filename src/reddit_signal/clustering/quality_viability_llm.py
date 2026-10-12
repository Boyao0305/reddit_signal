"""
Fused quality + viability LLM scorer for run_id=N's `is_candidate=TRUE` clusters.

For each candidate cluster, samples 20 representative pain statements, sends
them to MiniMax M2.7 with a single prompt that asks both:
  (a) cluster QUALITY  — is this a real, software-solvable pain?
  (b) SaaS VIABILITY   — buildability, ICP clarity, moat potential

Stores the answer in viability_scores (one row per (viability_run_id, cluster_id)).
No composite score — downstream consumers filter on individual columns.

Usage:
    python -m reddit_signal.clustering.quality_viability_llm --run-id 9 [--limit 10] [--workers 8]
    python -m reddit_signal.clustering.quality_viability_llm --run-id 9 --cluster-ids 123 456
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import psycopg2
import psycopg2.extras
from openai import OpenAI

from reddit_signal import runtime_settings
from reddit_signal.pain_extraction import db

log = logging.getLogger("quality_viability_llm")

PROMPT_VERSION = "v1"

DEFAULT_MODEL = runtime_settings.get_setting("VIABILITY_MODEL")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

VALID_NOISE_FLAGS = {"venting", "off_topic_drift", "duplicate_threads", "too_generic"}

_PROMPT = """\
You are evaluating a cluster of user pain points harvested from online B2C communities to decide:
  (a) whether it represents a REAL, SOFTWARE-SOLVABLE pain (cluster quality), and
  (b) whether it is a viable SaaS opportunity (cluster viability).

CLUSTER LABEL: {label}
TOP SUBREDDITS (count): {surface}

PAIN STATEMENTS (sample of {n} from {member_count} members):
{statements}

Evaluate carefully. Reject obvious mismatches: "lose weight", "find a partner",
"deal with anxiety" are real pains but NOT software-solvable. "I hate Mondays"
is venting. A cluster of complaints all from one viral thread is duplicate_threads.

Return ONLY a JSON object with EXACTLY these fields, no markdown, no code fences:
{{
  "quality": {{
    "is_real_pain":         true|false,
    "is_software_solvable": true|false,
    "specificity":          1|2|3|4|5,
    "noise_flag":           null | "venting" | "off_topic_drift" | "duplicate_threads" | "too_generic"
  }},
  "viability": {{
    "buildability":   1|2|3|4|5,
    "icp_clarity":    1|2|3|4|5,
    "moat_potential": 1|2|3|4|5,
    "rationale":      "<1-3 sentences justifying the quality+viability scores>"
  }}
}}

Scoring guide (1=worst, 5=best):
  - specificity:    1=vague venting, 5=concrete operational complaint with named tools/steps
  - buildability:   1=impossible/regulated/needs hardware, 5=weekend project for a solo dev
  - icp_clarity:    1=who is this for is unclear, 5=specific named ICP with clear willingness-to-pay
  - moat_potential: 1=trivial commodity, 5=defensible via network effects/data/integrations
"""

_db_lock = threading.Lock()


# ─── data access ─────────────────────────────────────────────────────────────

def _create_run(cluster_run_id: int, llm_model: str, params: dict) -> int:
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO viability_runs
                  (cluster_run_id, llm_model, prompt_version, params, n_clusters, n_succeeded, n_failed)
                VALUES (%s, %s, %s, %s, 0, 0, 0)
                RETURNING id
            """, (cluster_run_id, llm_model, PROMPT_VERSION, json.dumps(params)))
            run_id = cur.fetchone()[0]
        conn.commit()
        return run_id
    finally:
        db.put_conn(conn)


def _finalize_run(run_id: int, n_clusters: int, n_ok: int, n_fail: int):
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE viability_runs
                SET finished_at = NOW(),
                    n_clusters  = %s,
                    n_succeeded = %s,
                    n_failed    = %s
                WHERE id = %s
            """, (n_clusters, n_ok, n_fail, run_id))
        conn.commit()
    finally:
        db.put_conn(conn)


def _fetch_candidate_clusters(
    cluster_run_id: int,
    viability_run_id: int,
    cluster_ids: list[int] | None = None,
) -> list[dict]:
    """Candidates not yet scored by THIS viability_run_id."""
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cluster_filter = ""
            params: list[object] = [cluster_run_id, viability_run_id]
            if cluster_ids:
                cluster_filter = "AND pc.id = ANY(%s)"
                params.append(cluster_ids)
            cur.execute("""
                SELECT pc.id, pc.label, pc.member_count
                FROM pain_clusters pc
                WHERE pc.run_id = %s
                  AND pc.is_candidate
                  AND NOT EXISTS (
                    SELECT 1 FROM viability_scores vs
                    WHERE vs.viability_run_id = %s AND vs.cluster_id = pc.id
                  )
                  {cluster_filter}
                ORDER BY pc.member_count DESC NULLS LAST
            """.format(cluster_filter=cluster_filter), tuple(params))
            return [dict(r) for r in cur.fetchall()]
    finally:
        db.put_conn(conn)


def _fetch_sample(cluster_id: int, k: int = 20) -> tuple[list[int], list[str]]:
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT po.id, po.pain_statement
                FROM pain_objects po
                JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                WHERE pcm.cluster_id = %s
                ORDER BY po.community_score DESC NULLS LAST
                LIMIT %s
            """, (cluster_id, k))
            rows = cur.fetchall()
    finally:
        db.put_conn(conn)
    return [r["id"] for r in rows], [r["pain_statement"] for r in rows]


def _fetch_surface(cluster_id: int) -> list[tuple[str, int]]:
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT po.subreddit_name, COUNT(*) AS n
                FROM pain_objects po
                JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                WHERE pcm.cluster_id = %s
                GROUP BY po.subreddit_name
                ORDER BY n DESC
                LIMIT 5
            """, (cluster_id,))
            return [(r[0] or "(unknown)", int(r[1])) for r in cur.fetchall()]
    finally:
        db.put_conn(conn)


def _persist(run_id: int, cluster_id: int, parsed: dict, sample_ids: list[int],
             in_tokens: int, out_tokens: int, raw: str):
    q = parsed.get("quality") or {}
    v = parsed.get("viability") or {}

    nf = q.get("noise_flag")
    if isinstance(nf, str) and nf not in VALID_NOISE_FLAGS:
        nf = None
    if nf == "" :
        nf = None

    def _i(x, lo=1, hi=5):
        try:
            xi = int(x)
            if lo <= xi <= hi:
                return xi
        except Exception:
            pass
        return None

    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO viability_scores (
                    viability_run_id, cluster_id,
                    is_real_pain, is_software_solvable, q_specificity, q_noise_flag,
                    v_buildability, v_icp_clarity, v_moat_potential, v_rationale,
                    sample_pain_ids, llm_input_tokens, llm_output_tokens, raw_response
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (viability_run_id, cluster_id) DO UPDATE SET
                    is_real_pain         = EXCLUDED.is_real_pain,
                    is_software_solvable = EXCLUDED.is_software_solvable,
                    q_specificity        = EXCLUDED.q_specificity,
                    q_noise_flag         = EXCLUDED.q_noise_flag,
                    v_buildability       = EXCLUDED.v_buildability,
                    v_icp_clarity        = EXCLUDED.v_icp_clarity,
                    v_moat_potential     = EXCLUDED.v_moat_potential,
                    v_rationale          = EXCLUDED.v_rationale,
                    sample_pain_ids      = EXCLUDED.sample_pain_ids,
                    llm_input_tokens     = EXCLUDED.llm_input_tokens,
                    llm_output_tokens    = EXCLUDED.llm_output_tokens,
                    raw_response         = EXCLUDED.raw_response
            """, (
                run_id, cluster_id,
                bool(q.get("is_real_pain", False)),
                bool(q.get("is_software_solvable", False)),
                _i(q.get("specificity")),
                nf,
                _i(v.get("buildability")),
                _i(v.get("icp_clarity")),
                _i(v.get("moat_potential")),
                v.get("rationale", "")[:5000],
                sample_ids,
                in_tokens, out_tokens,
                json.dumps({"raw": raw}),
            ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


# ─── LLM call ────────────────────────────────────────────────────────────────

def _build_prompt(label: str, member_count: int, statements: list[str], surface: list[tuple[str,int]]) -> str:
    surface_str = ", ".join(f"r/{s} ({n})" for s, n in surface) or "(none)"
    stmt_lines = "\n".join(f"  {i+1}. {s.strip()[:300]}" for i, s in enumerate(statements))
    return _PROMPT.format(
        label=label or "(unlabeled)",
        surface=surface_str,
        n=len(statements),
        member_count=member_count,
        statements=stmt_lines,
    )


def _extract_json(txt: str) -> dict:
    """Robustly parse JSON from LLM output that may include reasoning, fences, or truncation."""
    import re
    s = txt.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # strip code fences
    cleaned = s.strip("` \n")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # find the largest balanced {...} block
    starts = [i for i, ch in enumerate(s) if ch == "{"]
    for start in starts:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start:i+1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
    raise json.JSONDecodeError("could not extract JSON", txt, 0)


def _call_llm(client: OpenAI, model: str, prompt: str, max_retries: int = 3) -> tuple[dict, int, int, str]:
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=4000,   # MiniMax M2.7 is a reasoning model — reasoning tokens eat budget
            )
            txt = (resp.choices[0].message.content or "").strip()
            reasoning = getattr(resp.choices[0].message, "reasoning", None) or ""
            # Fall back to reasoning text if content empty (some routers do that)
            source = txt if txt else reasoning
            parsed = _extract_json(source)
            usage = resp.usage
            return parsed, getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0), source
        except Exception as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_exc}")


# ─── orchestration ───────────────────────────────────────────────────────────

def score_run(
    cluster_run_id: int,
    workers: int = 8,
    model: str | None = None,
    limit: Optional[int] = None,
    viability_run_id: Optional[int] = None,
    cluster_ids: list[int] | None = None,
) -> dict:
    model = model or runtime_settings.get_setting("VIABILITY_MODEL")
    api_key = runtime_settings.get_setting("OPENROUTER_API_KEY").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)

    if viability_run_id is None:
        viability_run_id = _create_run(
            cluster_run_id, model,
            params={"workers": workers, "limit": limit, "sample_k": 20},
        )
        log.info("Created viability_run_id=%d for cluster_run_id=%d", viability_run_id, cluster_run_id)

    clusters = _fetch_candidate_clusters(cluster_run_id, viability_run_id, cluster_ids=cluster_ids)
    if limit:
        clusters = clusters[:limit]
    log.info("Scoring %d candidate clusters (model=%s, workers=%d)", len(clusters), model, workers)

    n_ok = n_fail = 0
    failures = []

    def _worker(c):
        cid = c["id"]
        try:
            sample_ids, stmts = _fetch_sample(cid, k=20)
            if not stmts:
                return cid, "no statements"
            surface = _fetch_surface(cid)
            prompt = _build_prompt(c["label"], c["member_count"], stmts, surface)
            parsed, in_tok, out_tok, raw = _call_llm(client, model, prompt)
            with _db_lock:
                _persist(viability_run_id, cid, parsed, sample_ids, in_tok, out_tok, raw)
            return cid, "ok"
        except Exception as exc:
            return cid, f"FAIL: {exc!r}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, c) for c in clusters]
        for i, fut in enumerate(as_completed(futures)):
            cid, status = fut.result()
            if status == "ok":
                n_ok += 1
            else:
                n_fail += 1
                failures.append((cid, status))
            if (i + 1) % 25 == 0 or (i + 1) == len(clusters):
                log.info("  %d/%d processed (ok=%d, fail=%d)", i+1, len(clusters), n_ok, n_fail)

    _finalize_run(viability_run_id, len(clusters), n_ok, n_fail)
    return {
        "viability_run_id": viability_run_id,
        "ok": n_ok,
        "fail": n_fail,
        "failures": failures[:20],
    }


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True, help="cluster_run_id")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--viability-run-id", type=int, default=None,
                    help="Resume an existing viability run instead of creating a new one.")
    ap.add_argument("--cluster-ids", type=int, nargs="*", default=None,
                    help="Only score these candidate cluster ids.")
    args = ap.parse_args()

    stats = score_run(
        cluster_run_id=args.run_id,
        workers=args.workers,
        model=args.model,
        limit=args.limit,
        viability_run_id=args.viability_run_id,
        cluster_ids=args.cluster_ids,
    )
    log.info("Result: %s", stats)


if __name__ == "__main__":
    main()
