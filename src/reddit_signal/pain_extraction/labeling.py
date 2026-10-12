"""
LLM-based cluster labeling.

Sends top-N diverse member statements to Gemini Flash Lite,
asks for a concise 10-15 word theme summary per cluster.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import numpy as np
import psycopg2.extras
from openai import OpenAI
from sklearn.metrics.pairwise import cosine_similarity

from . import db
from .config import get_openrouter_api_key, get_openrouter_model

logger = logging.getLogger(__name__)


LABEL_PROMPT = """You are summarizing a cluster of user pain points from online business/tech communities.

Here are representative pain statements from this cluster:
{statements}

Write a single concise label (10-15 words max) that captures the CORE shared pain theme.
Format: "[Domain]: [core problem description]"

Examples:
- "Sales outreach: founders waste hours on manual prospecting with low reply rates"
- "IT security: organizations lack unified tooling to detect credential theft"
- "SaaS pricing: users frustrated by hidden fees and confusing tier structures"
- "Content creation: AI-generated text misses intent and requires heavy editing"

Return ONLY the label, nothing else."""


def _get_client() -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=get_openrouter_api_key(),
    )


def _select_diverse_members(
    cluster_id: int,
    top_n: int = 10,
    run_id: Optional[int] = None,
) -> List[str]:
    """
    Select top_n diverse pain statements from a cluster using MMR.
    Returns list of pain_statement strings.
    Uses pain_cluster_members join when run_id is given, legacy cluster_id otherwise.
    """
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # All clusters resolve members via pain_cluster_members (post-Phase-1).
            cur.execute("""
                SELECT po.id, po.pain_statement, po.embedding::text
                FROM pain_objects po
                JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                WHERE pcm.cluster_id = %s AND po.embedding IS NOT NULL
                ORDER BY po.community_score DESC NULLS LAST
                LIMIT 50
            """, (cluster_id,))
            rows = cur.fetchall()
    finally:
        db.put_conn(conn)

    if not rows:
        return []

    # Parse embeddings
    texts = [r["pain_statement"] for r in rows]
    vecs = []
    for r in rows:
        vec_str = r["embedding"].strip("[]")
        vec = np.fromstring(vec_str, sep=",", dtype=np.float32)
        vecs.append(vec)
    vecs = np.array(vecs)

    if len(rows) <= top_n:
        return texts

    # Compute centroid
    centroid = vecs.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm

    centroid_sims = cosine_similarity(vecs, centroid.reshape(1, -1)).flatten()

    # MMR selection
    selected = []
    remaining = list(range(len(rows)))

    for _ in range(top_n):
        if not remaining:
            break
        if not selected:
            best = max(remaining, key=lambda i: centroid_sims[i])
        else:
            selected_vecs = vecs[selected]
            best_score = -float("inf")
            best = remaining[0]
            for i in remaining:
                relevance = centroid_sims[i]
                diversity_penalty = cosine_similarity(
                    vecs[i].reshape(1, -1), selected_vecs
                ).max()
                mmr = 0.5 * relevance - 0.5 * diversity_penalty
                if mmr > best_score:
                    best_score = mmr
                    best = i
            selected.append(best)
            remaining.remove(best)
        if best not in selected:
            selected.append(best)
            remaining.remove(best)

    return [texts[i] for i in selected]


def _generate_label(client: OpenAI, cluster_id: int, statements: List[str], model_id: str) -> str:
    """Call LLM to generate a concise cluster label."""
    numbered = "\n".join(f"{i+1}. {s[:200]}" for i, s in enumerate(statements))
    prompt = LABEL_PROMPT.format(statements=numbered)

    try:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.3,
        )
        label = resp.choices[0].message.content.strip()
        # Clean up: remove quotes, markdown
        label = label.strip('"\'`')
        if label.startswith("- "):
            label = label[2:]
        return label
    except Exception as e:
        logger.warning("LLM labeling failed for cluster %d: %s", cluster_id, e)
        return ""


def label_all_clusters(max_workers: int = 4, run_id: Optional[int] = None) -> dict:
    """
    Generate LLM labels for clusters.
    If run_id is given: labels only that run's clusters.
    Returns {cluster_db_id: label_text}.
    """
    # Cap workers so we never exhaust the DB pool even if called with high defaults.
    effective_workers = min(max_workers, 4)
    if effective_workers != max_workers:
        logger.warning(
            "label_all_clusters max_workers capped from %d to %d to avoid DB pool exhaustion",
            max_workers, effective_workers,
        )
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if run_id is not None:
                cur.execute(
                    "SELECT id, member_count FROM pain_clusters WHERE run_id = %s ORDER BY id",
                    (run_id,),
                )
            else:
                cur.execute("SELECT id, member_count FROM pain_clusters ORDER BY id")
            clusters = [dict(r) for r in cur.fetchall()]
    finally:
        db.put_conn(conn)

    if not clusters:
        print("No clusters found.")
        return {}

    model_id = get_openrouter_model()
    print(f"Generating LLM labels for {len(clusters)} clusters "
          f"(model: {model_id}, workers: {effective_workers})...")

    client = _get_client()
    labels = {}
    done = 0
    errors = 0

    def process_cluster(cluster):
        cid = cluster["id"]
        statements = _select_diverse_members(cid, top_n=10, run_id=run_id)
        if not statements:
            return cid, ""
        label = _generate_label(client, cid, statements, model_id)
        return cid, label

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {executor.submit(process_cluster, c): c for c in clusters}
        for future in as_completed(futures):
            cid, label = future.result()
            labels[cid] = label
            done += 1
            if not label:
                errors += 1
            if done % 50 == 0:
                print(f"  [{done}/{len(clusters)}] labeled, {errors} errors")

    print(f"Done. {done} clusters labeled, {errors} errors.")

    # Save to DB
    _save_labels(labels)

    return labels


def _save_labels(labels: dict):
    """Write LLM-generated labels back to pain_clusters."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            for cid, label in labels.items():
                if label:
                    cur.execute(
                        "UPDATE pain_clusters SET label = %s WHERE id = %s",
                        (label, cid),
                    )
        conn.commit()
        logger.info("Saved %d LLM labels to pain_clusters.", len(labels))
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)
