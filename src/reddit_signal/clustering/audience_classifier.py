"""
Per-cluster B2B/B2C audience classifier (Phase 1 task 1.19).

For each cluster of an analysis_runs entry, this module:
  1. Samples diverse pain statements (reuses labeling._select_diverse_members)
  2. Computes the surface (subreddit) distribution
  3. Calls the LLM (OpenRouter) with the statements + subreddit_audience_prior
     hints to classify the cluster as B2B / B2C / mixed / unclear
  4. Persists to cluster_audience (idempotent on (cluster_id, classifier_version))

Design notes:
  - The audience_prior of a subreddit is a WEAK signal — the LLM is told the
    distribution and can override.
  - "mixed" is a valid, non-failure outcome — use this for clusters that
    genuinely span both audiences.
  - confidence ∈ [0, 1] is from the model. Downstream gating (is_candidate
    in Phase 1 v2) uses this to filter B2C clusters with conf >= 0.6.

Usage:
    python -m reddit_signal.clustering.audience_classifier --run-id N [--workers 10]
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

# Ensure repo root on path when invoked as script
REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import psycopg2
import psycopg2.extras
from openai import OpenAI

from reddit_signal import runtime_settings
from reddit_signal.pain_extraction import db, labeling

log = logging.getLogger("audience_classifier")

CLASSIFIER_VERSION = "v1"

# Default to a small, cheap, fast model. Override via env.
DEFAULT_MODEL = runtime_settings.get_setting("AUDIENCE_CLASSIFIER_MODEL")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_PROMPT = """\
You are classifying which audience a cluster of user complaints/needs is for.

A cluster of pain points is shown below as 15-25 representative statements,
along with the distribution of source subreddits they came from.

Classify the cluster as ONE of:
  - "B2B": professional/work context. Buyers are businesses/teams. Examples:
    "our team can't sync schedules", "our CRM is broken", "compliance reporting".
  - "B2C": personal life. Consumer apps, family/health/relationships/finance.
    Examples: "my fitness tracker resets", "I can't find a budgeting app",
    "dating app matches keep ghosting".
  - "mixed": the cluster genuinely contains both. Common in entrepreneur/SaaS
    forums where founders talk about both their work AND their personal stack.
  - "unclear": insufficient signal to decide.

Source subreddit hints (each subreddit has a default audience_prior — a WEAK signal,
not a label; trust the statements themselves first):
{surface_hints}

Sample pain statements:
{statements}

Return ONLY a JSON object with EXACTLY these fields, no markdown, no code fences:
{{
  "audience": "B2B" | "B2C" | "mixed" | "unclear",
  "confidence": <float 0..1>,
  "icp_hint": "<one short phrase describing the ideal customer profile>",
  "reasoning": "<1-2 sentence justification>"
}}
"""

_db_lock = threading.Lock()


# ─── data access ─────────────────────────────────────────────────────────────

def _fetch_clusters_to_classify(run_id: int) -> list[dict]:
    """Return clusters of `run_id` that don't yet have a cluster_audience row
    for the current classifier_version."""
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT pc.id, pc.label, pc.member_count
                FROM pain_clusters pc
                WHERE pc.run_id = %s
                  AND NOT EXISTS (
                    SELECT 1 FROM cluster_audience ca
                    WHERE ca.cluster_id = pc.id
                      AND ca.classifier_version = %s
                  )
                ORDER BY pc.member_count DESC NULLS LAST
            """, (run_id, CLASSIFIER_VERSION))
            return [dict(r) for r in cur.fetchall()]
    finally:
        db.put_conn(conn)


def _fetch_surface_distribution(cluster_id: int) -> dict[str, dict]:
    """Return {subreddit_name: {count: N, audience_prior: 'B2B'|'B2C'|'mixed'|'unknown'}}"""
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT po.subreddit_name AS sub,
                       COUNT(*) AS n,
                       MAX(s.audience_prior) AS prior
                FROM pain_objects po
                JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                LEFT JOIN subreddits s ON s.name = po.subreddit_name
                WHERE pcm.cluster_id = %s
                GROUP BY po.subreddit_name
                ORDER BY n DESC
                LIMIT 10
            """, (cluster_id,))
            rows = cur.fetchall()
            return {
                r["sub"] or "(unknown)": {"count": int(r["n"]), "audience_prior": r["prior"] or "unknown"}
                for r in rows
            }
    finally:
        db.put_conn(conn)


def _fetch_sample_pain_ids(cluster_id: int, k: int = 20) -> tuple[list[int], list[str]]:
    """Return (sample_pain_ids, statements). Reuses labeling diverse-member sampler."""
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
    ids = [r["id"] for r in rows]
    stmts = [r["pain_statement"] for r in rows]
    return ids, stmts


def _persist_audience(
    cluster_id: int,
    parsed: dict,
    sample_ids: list[int],
    surface_dist: dict,
    model: str,
    in_tokens: int,
    out_tokens: int,
):
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cluster_audience (
                    cluster_id, classifier_version, audience_type, confidence,
                    icp_hint, reasoning, sample_pain_ids, surface_distribution,
                    llm_model, llm_input_tokens, llm_output_tokens
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cluster_id, classifier_version) DO UPDATE SET
                    audience_type = EXCLUDED.audience_type,
                    confidence    = EXCLUDED.confidence,
                    icp_hint      = EXCLUDED.icp_hint,
                    reasoning     = EXCLUDED.reasoning,
                    sample_pain_ids      = EXCLUDED.sample_pain_ids,
                    surface_distribution = EXCLUDED.surface_distribution,
                    llm_model            = EXCLUDED.llm_model,
                    llm_input_tokens     = EXCLUDED.llm_input_tokens,
                    llm_output_tokens    = EXCLUDED.llm_output_tokens
            """, (
                cluster_id, CLASSIFIER_VERSION,
                parsed.get("audience", "unclear"),
                float(parsed.get("confidence", 0.0) or 0.0),
                parsed.get("icp_hint", ""),
                parsed.get("reasoning", ""),
                sample_ids,
                json.dumps(surface_dist),
                model,
                in_tokens,
                out_tokens,
            ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


# ─── LLM call ────────────────────────────────────────────────────────────────

def _build_prompt(statements: list[str], surface_dist: dict) -> str:
    surface_lines = []
    for sub, info in list(surface_dist.items())[:10]:
        surface_lines.append(
            f"  - r/{sub}: {info['count']} pains  [audience_prior={info['audience_prior']}]"
        )
    statement_lines = "\n".join(f"  {i+1}. {s.strip()[:300]}" for i, s in enumerate(statements))
    return _PROMPT.format(
        surface_hints="\n".join(surface_lines) or "  (none)",
        statements=statement_lines,
    )


def _call_llm(client: OpenAI, model: str, prompt: str, max_retries: int = 3) -> tuple[dict, int, int]:
    """Returns (parsed_json, input_tokens, output_tokens)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=400,
            )
            txt = (resp.choices[0].message.content or "").strip()
            try:
                parsed = json.loads(txt)
            except json.JSONDecodeError:
                # try stripping common markdown fences
                cleaned = txt.strip("` \n")
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                parsed = json.loads(cleaned)
            usage = resp.usage
            return parsed, getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0)
        except Exception as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_exc}")


# ─── orchestration ───────────────────────────────────────────────────────────

def classify_run(run_id: int, workers: int = 10, model: str | None = None,
                 max_clusters: Optional[int] = None) -> dict:
    """Classify every (yet-unclassified) cluster of `run_id`."""
    model = model or runtime_settings.get_setting("AUDIENCE_CLASSIFIER_MODEL")
    api_key = runtime_settings.get_setting("OPENROUTER_API_KEY").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)

    clusters = _fetch_clusters_to_classify(run_id)
    if max_clusters:
        clusters = clusters[:max_clusters]
    log.info("Classifying %d clusters for run_id=%d (model=%s, workers=%d)",
             len(clusters), run_id, model, workers)

    stats = {"ok": 0, "fail": 0, "by_audience": {"B2B": 0, "B2C": 0, "mixed": 0, "unclear": 0}}

    def process(cluster):
        cid = cluster["id"]
        try:
            sample_ids, stmts = _fetch_sample_pain_ids(cid, k=20)
            if not stmts:
                return cid, None, "no statements"
            surface = _fetch_surface_distribution(cid)
            prompt = _build_prompt(stmts, surface)
            parsed, in_tok, out_tok = _call_llm(client, model, prompt)
            with _db_lock:
                _persist_audience(cid, parsed, sample_ids, surface, model, in_tok, out_tok)
            return cid, parsed.get("audience", "unclear"), None
        except Exception as exc:
            log.warning("cluster %d failed: %s", cid, exc)
            return cid, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(process, c) for c in clusters]
        for i, fut in enumerate(as_completed(futures), 1):
            cid, audience, err = fut.result()
            if err:
                stats["fail"] += 1
            else:
                stats["ok"] += 1
                if audience in stats["by_audience"]:
                    stats["by_audience"][audience] += 1
            if i % 50 == 0:
                log.info("  …%d/%d clusters processed; by_audience=%s",
                         i, len(clusters), stats["by_audience"])

    log.info("Done: ok=%d, fail=%d, by_audience=%s", stats["ok"], stats["fail"], stats["by_audience"])
    return stats


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True,
                    help="analysis_runs.id to classify clusters for")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-clusters", type=int, default=None,
                    help="Only classify the first N clusters (testing)")
    args = ap.parse_args()
    classify_run(args.run_id, workers=args.workers, model=args.model,
                 max_clusters=args.max_clusters)


if __name__ == "__main__":
    main()
