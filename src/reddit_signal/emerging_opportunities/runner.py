"""Deterministic orphan-pain micro-clustering."""
from __future__ import annotations

import logging
import math
import traceback
from datetime import date
from typing import Any

import numpy as np
from sklearn.cluster import DBSCAN

from . import repository

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MIN_SIZE = 8
DEFAULT_MIN_AUTHORS = 3
DEFAULT_MIN_SUBREDDITS = 2
DEFAULT_EPS = 0.18
DEFAULT_MIN_COHERENCE = 0.72


def parse_vector(value: str) -> np.ndarray:
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return np.fromstring(text, sep=",", dtype=np.float32)


def _normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], np.ndarray]:
    normalized: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    for row in rows:
        vec = parse_vector(str(row.get("embedding") or ""))
        if vec.size == 0:
            continue
        norm = float(np.linalg.norm(vec))
        if norm <= 0 or math.isnan(norm):
            continue
        vec = vec / norm
        item = dict(row)
        item["vector"] = vec
        normalized.append(item)
        vectors.append(vec)
    if not vectors:
        return [], np.zeros((0, 384), dtype=np.float32)
    return normalized, np.vstack(vectors).astype(np.float32)


def _label_for_cluster(rows: list[dict[str, Any]], centroid: np.ndarray) -> str:
    best = max(rows, key=lambda r: float(np.dot(r["vector"], centroid)))
    label = str(best.get("pain_statement") or "").strip()
    if len(label) > 96:
        label = label[:93].rstrip() + "..."
    return label or f"Emerging opportunity ({len(rows)} pains)"


def _sample_quotes(rows: list[dict[str, Any]], centroid: np.ndarray, limit: int = 3) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda r: float(np.dot(r["vector"], centroid)), reverse=True)
    out: list[dict[str, Any]] = []
    for row in ranked[:limit]:
        source_created = row.get("source_created")
        out.append(
            {
                "pain_id": row["id"],
                "pain_statement": row.get("pain_statement"),
                "raw_quote": row.get("raw_quote"),
                "subreddit_name": row.get("subreddit_name"),
                "source_author": row.get("source_author"),
                "source_created": source_created.isoformat() if source_created else None,
            }
        )
    return out


def build_opportunities(
    rows: list[dict[str, Any]],
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    min_authors: int = DEFAULT_MIN_AUTHORS,
    min_subreddits: int = DEFAULT_MIN_SUBREDDITS,
    eps: float = DEFAULT_EPS,
    min_coherence: float = DEFAULT_MIN_COHERENCE,
) -> list[dict[str, Any]]:
    """Return filtered deterministic opportunities from orphan rows."""
    normalized, matrix = _normalize_rows(rows)
    if len(normalized) < min_size:
        return []

    labels = DBSCAN(eps=eps, min_samples=min_size, metric="cosine").fit_predict(matrix)
    opportunities: list[dict[str, Any]] = []

    for label_id in sorted({int(x) for x in labels if int(x) >= 0}):
        idxs = np.where(labels == label_id)[0]
        if len(idxs) < min_size:
            continue
        cluster_rows = [normalized[int(i)] for i in idxs]
        authors = {r.get("source_author") or "" for r in cluster_rows if r.get("source_author")}
        subreddits = {r.get("subreddit_name") or "" for r in cluster_rows if r.get("subreddit_name")}
        if len(authors) < min_authors or len(subreddits) < min_subreddits:
            continue

        cluster_matrix = matrix[idxs]
        centroid = cluster_matrix.mean(axis=0)
        centroid_norm = float(np.linalg.norm(centroid))
        if centroid_norm <= 0:
            continue
        centroid = centroid / centroid_norm
        similarities = cluster_matrix @ centroid
        coherence = float(np.mean(similarities))
        if coherence < min_coherence:
            continue

        dates = [
            r["source_created"].date()
            for r in cluster_rows
            if r.get("source_created") is not None
        ]
        source_window_start: date | None = min(dates) if dates else None
        source_window_end: date | None = max(dates) if dates else None
        pain_count = len(cluster_rows)
        burst_score = min(1.0, (pain_count / max(min_size * 2.0, 1.0)) * coherence)
        sample_quotes = _sample_quotes(cluster_rows, centroid)
        sample_pain_ids = [int(item["pain_id"]) for item in sample_quotes]
        members = [
            {
                "pain_id": int(row["id"]),
                "similarity": float(similarities[pos]),
            }
            for pos, row in enumerate(cluster_rows)
        ]

        opportunities.append(
            {
                "label": _label_for_cluster(cluster_rows, centroid),
                "pain_count": pain_count,
                "author_count": len(authors),
                "subreddit_count": len(subreddits),
                "coherence": coherence,
                "burst_score": burst_score,
                "source_window_start": source_window_start,
                "source_window_end": source_window_end,
                "sample_pain_ids": sample_pain_ids,
                "sample_quotes": sample_quotes,
                "members": members,
            }
        )

    opportunities.sort(
        key=lambda opp: (opp["burst_score"], opp["coherence"], opp["pain_count"]),
        reverse=True,
    )
    return opportunities


def run_emerging_opportunities(
    *,
    cluster_run_id: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_size: int = DEFAULT_MIN_SIZE,
    min_authors: int = DEFAULT_MIN_AUTHORS,
    min_subreddits: int = DEFAULT_MIN_SUBREDDITS,
    eps: float = DEFAULT_EPS,
    min_coherence: float = DEFAULT_MIN_COHERENCE,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = repository.list_orphan_pains(
        cluster_run_id=cluster_run_id,
        lookback_days=lookback_days,
        limit=limit,
    )
    opportunities = build_opportunities(
        rows,
        min_size=min_size,
        min_authors=min_authors,
        min_subreddits=min_subreddits,
        eps=eps,
        min_coherence=min_coherence,
    )
    if dry_run:
        return {
            "dry_run": True,
            "cluster_run_id": cluster_run_id,
            "candidate_count": len(rows),
            "opportunity_count": len(opportunities),
            "opportunities": opportunities,
        }

    emerging_run_id = repository.create_run(
        cluster_run_id=cluster_run_id,
        lookback_days=lookback_days,
        min_size=min_size,
        eps=eps,
        candidate_count=len(rows),
    )
    try:
        inserted = repository.save_opportunities(
            emerging_run_id=emerging_run_id,
            cluster_run_id=cluster_run_id,
            opportunities=opportunities,
        )
        repository.finish_run(
            emerging_run_id=emerging_run_id,
            opportunity_count=inserted,
        )
        return {
            "dry_run": False,
            "emerging_run_id": emerging_run_id,
            "cluster_run_id": cluster_run_id,
            "candidate_count": len(rows),
            "opportunity_count": inserted,
        }
    except Exception as exc:
        repository.fail_run(
            emerging_run_id=emerging_run_id,
            error_log=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )
        raise

