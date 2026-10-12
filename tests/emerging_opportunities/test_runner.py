from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from reddit_signal.emerging_opportunities import runner


def _vec_text(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _row(i: int, vec: np.ndarray, *, author: str, subreddit: str) -> dict:
    return {
        "id": i,
        "pain_statement": f"Pain statement {i}",
        "raw_quote": f"Raw quote {i}",
        "subreddit_name": subreddit,
        "source_author": author,
        "source_created": datetime(2026, 6, 20, tzinfo=timezone.utc),
        "embedding": _vec_text(vec),
    }


def test_build_opportunities_filters_and_scores_cluster():
    base = np.zeros(384, dtype=np.float32)
    base[0] = 1.0
    rows = []
    for i in range(8):
        vec = base.copy()
        vec[i + 1] = 0.01
        vec = vec / np.linalg.norm(vec)
        rows.append(
            _row(
                i + 1,
                vec,
                author=f"user{i % 4}",
                subreddit=f"sub{i % 2}",
            )
        )

    opportunities = runner.build_opportunities(
        rows,
        min_size=8,
        min_authors=3,
        min_subreddits=2,
        eps=0.05,
        min_coherence=0.9,
    )

    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp["pain_count"] == 8
    assert opp["author_count"] == 4
    assert opp["subreddit_count"] == 2
    assert opp["coherence"] > 0.9
    assert len(opp["sample_pain_ids"]) == 3


def test_run_dry_run_does_not_write(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.repository, "list_orphan_pains", lambda **kwargs: [])
    monkeypatch.setattr(
        runner.repository,
        "create_run",
        lambda **kwargs: calls.append(kwargs) or 123,
    )

    result = runner.run_emerging_opportunities(cluster_run_id=10, dry_run=True)

    assert result["dry_run"] is True
    assert result["candidate_count"] == 0
    assert calls == []

