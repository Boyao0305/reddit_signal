"""Path C ranking acceptance tests (spec §5.2).

Exercises v_market_opportunities_ranked / v_market_opportunities_pregate (run 10,
viability 4, B2C) directly over the DB plus the /api/v2/clusters surface.

Connection: os.environ['DATABASE_URL'] — the caller is responsible for sourcing
.env before invoking pytest (e.g. `set -a && source .env && set +a`). Each DB test
sets the three GUCs explicitly inside its own transaction and rolls back.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from reddit_signal.dashboard.api import app  # noqa: E402


RUN_ID = 10
VIABILITY_RUN_ID = 4
ACV = "v1"
AUDIENCE = "B2C"
EPS = 0.05

PATH_C_COLS = {
    "pareto_layer",
    "balance_score",
    "q_axis",
    "m_axis",
    "s_axis",
    "is_eligible",
    "low_confidence_s",
}

# The legacy v_market_opportunities backward-compat projection (migration §4).
LEGACY_COLS = [
    "cluster_id", "cluster_run_id", "label", "member_count", "coherence",
    "is_candidate", "audience_classifier_version", "audience_type",
    "audience_confidence", "icp_hint", "thread_diversity", "author_diversity",
    "subreddit_count", "specificity_mean", "workaround_ratio", "separation",
    "temporal_spread_days", "source_diversity", "viability_run_id", "is_real_pain",
    "is_software_solvable", "q_specificity", "q_noise_flag", "v_buildability",
    "v_icp_clarity", "v_moat_potential", "v_rationale", "pain_count_last_4w",
    "pain_count_prior_4w", "weeks_with_data", "total_pain", "slope_per_week",
    "velocity_4w_ratio", "velocity_4w_ratio_normalized",
]


@pytest.fixture(scope="module")
def conn(readonly_dsn):
    c = psycopg2.connect(readonly_dsn)
    yield c
    c.close()


@contextmanager
def guc_cursor(conn):
    """Yield a RealDictCursor with the three Path C GUCs SET LOCAL; always rollback."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL llm_analyse.cluster_run_id = %s", (str(RUN_ID),))
        cur.execute("SET LOCAL llm_analyse.viability_run_id = %s", (str(VIABILITY_RUN_ID),))
        cur.execute("SET LOCAL llm_analyse.audience_classifier_version = %s", (ACV,))
        yield cur
    finally:
        conn.rollback()
        cur.close()


@pytest.fixture(scope="module")
def client(configured_readonly_db):
    # The analytics API is read-only and unauthenticated (ADR 0003).
    return TestClient(app)


# ---- §5.2.1 ---------------------------------------------------------------

def test_ranked_row_count_and_columns(conn):
    with guc_cursor(conn) as cur:
        cur.execute(
            "SELECT * FROM v_market_opportunities_ranked WHERE audience_type = %s",
            (AUDIENCE,),
        )
        rows = cur.fetchall()
    assert len(rows) == 740, f"expected 740 B2C ranked rows, got {len(rows)}"
    for r in rows:
        assert PATH_C_COLS.issubset(r.keys()), (
            f"cluster {r['cluster_id']} missing path-C cols: {PATH_C_COLS - set(r.keys())}"
        )


# ---- §5.2.2 ---------------------------------------------------------------

def test_layer1_nonempty_and_non_dominated(conn):
    with guc_cursor(conn) as cur:
        cur.execute(
            """
            SELECT cluster_id, q_axis, m_axis, s_axis
            FROM v_market_opportunities_ranked
            WHERE audience_type = %s AND pareto_layer = 1
            """,
            (AUDIENCE,),
        )
        layer1 = cur.fetchall()
    assert len(layer1) >= 5, f"layer 1 too small: {len(layer1)}"

    def dominates(b, a):
        return (
            b["q_axis"] >= a["q_axis"]
            and b["m_axis"] >= a["m_axis"]
            and b["s_axis"] >= a["s_axis"]
            and (
                b["q_axis"] > a["q_axis"]
                or b["m_axis"] > a["m_axis"]
                or b["s_axis"] > a["s_axis"]
            )
        )

    for a in layer1:
        for b in layer1:
            if a["cluster_id"] == b["cluster_id"]:
                continue
            assert not dominates(b, a), (
                f"layer-1 cluster {b['cluster_id']} dominates layer-1 {a['cluster_id']}"
            )


# ---- §5.2.3 ---------------------------------------------------------------

def test_eligibility_matches_within_audience_p25(conn):
    with guc_cursor(conn) as cur:
        cur.execute(
            """
            SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY q_axis) AS q_p25,
                   percentile_cont(0.25) WITHIN GROUP (ORDER BY s_axis) AS s_p25
            FROM v_market_opportunities_ranked
            WHERE audience_type = %s
            """,
            (AUDIENCE,),
        )
        p = cur.fetchone()
        q_p25, s_p25 = p["q_p25"], p["s_p25"]
        cur.execute(
            """
            SELECT cluster_id, q_axis, s_axis
            FROM v_market_opportunities_ranked
            WHERE audience_type = %s AND is_eligible IS TRUE
            """,
            (AUDIENCE,),
        )
        eligible = cur.fetchall()
    assert eligible, "expected at least some eligible rows"
    tol = 1e-9
    for r in eligible:
        assert r["q_axis"] >= q_p25 - tol, f"cluster {r['cluster_id']} q<{q_p25}"
        assert r["s_axis"] >= s_p25 - tol, f"cluster {r['cluster_id']} s<{s_p25}"


# ---- §5.2.4 ---------------------------------------------------------------

def test_zero_floor_nulls_balance_and_layer(conn):
    with guc_cursor(conn) as cur:
        cur.execute(
            """
            SELECT cluster_id, balance_score, pareto_layer
            FROM v_market_opportunities_ranked
            WHERE audience_type = %s
              AND (q_axis < %s OR m_axis < %s OR s_axis < %s)
            """,
            (AUDIENCE, EPS, EPS, EPS),
        )
        floored = cur.fetchall()
    assert floored, "expected some rows with an axis below epsilon"
    for r in floored:
        assert r["balance_score"] is None, f"cluster {r['cluster_id']} balance not NULL"
        assert r["pareto_layer"] is None, f"cluster {r['cluster_id']} layer not NULL"


# ---- §5.2.5 ---------------------------------------------------------------

def test_harmonic_mean_value(conn):
    with guc_cursor(conn) as cur:
        cur.execute(
            """
            SELECT cluster_id, q_axis, m_axis, s_axis, balance_score
            FROM v_market_opportunities_ranked
            WHERE audience_type = %s
              AND q_axis >= 0.5 AND m_axis >= 0.5 AND s_axis >= 0.5
              AND balance_score IS NOT NULL
            LIMIT 1
            """,
            (AUDIENCE,),
        )
        r = cur.fetchone()
    assert r is not None, "expected a row with all axes >= 0.5"
    expected = 3.0 / (1.0 / r["q_axis"] + 1.0 / r["m_axis"] + 1.0 / r["s_axis"])
    assert abs(r["balance_score"] - expected) < 1e-3, (
        f"cluster {r['cluster_id']}: balance {r['balance_score']} != {expected}"
    )


# ---- §5.2.6 ---------------------------------------------------------------

def test_low_confidence_s_member_count(conn):
    with guc_cursor(conn) as cur:
        cur.execute(
            """
            SELECT cluster_id
            FROM v_market_opportunities_ranked
            WHERE audience_type = %s AND low_confidence_s IS TRUE
            LIMIT 15
            """,
            (AUDIENCE,),
        )
        ids = [r["cluster_id"] for r in cur.fetchall()]
        assert ids, "expected at least one low_confidence_s row"
        for cid in ids:
            cur.execute(
                """
                SELECT COUNT(*) FILTER (WHERE array_length(po.mentioned_tools, 1) > 0)
                       AS members_with_tools
                FROM pain_cluster_members pcm
                JOIN pain_objects po ON po.id = pcm.pain_id
                JOIN pain_clusters pc ON pc.id = pcm.cluster_id
                WHERE pc.run_id = %s AND pcm.cluster_id = %s
                """,
                (RUN_ID, cid),
            )
            n = cur.fetchone()["members_with_tools"]
            assert n < 20, f"cluster {cid} low_confidence_s but {n} tool-mentioning members"


# ---- §5.2.7 ---------------------------------------------------------------

def test_legacy_projection_unchanged(conn):
    with guc_cursor(conn) as cur:
        cur.execute("SELECT * FROM v_market_opportunities WHERE audience_type = %s LIMIT 0", (AUDIENCE,))
        legacy_cols = [d.name for d in cur.description]
        cur.execute("SELECT count(*) AS n FROM v_market_opportunities WHERE audience_type = %s", (AUDIENCE,))
        legacy_n = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM v_market_opportunities_ranked WHERE audience_type = %s", (AUDIENCE,))
        ranked_n = cur.fetchone()["n"]
    assert legacy_cols == LEGACY_COLS, f"legacy projection drift: {legacy_cols}"
    assert not (PATH_C_COLS & set(legacy_cols)), "path-C columns leaked into legacy shim"
    assert legacy_n == ranked_n == 740


# ---- §5.2.8 ---------------------------------------------------------------

def test_api_clusters_sorted_layer_then_balance(client, conn):
    from tests.test_dashboard_v2_api import fetch_all_clusters

    clusters = fetch_all_clusters(
        client,
        cluster_run_id=RUN_ID,
        viability_run_id=VIABILITY_RUN_ID,
        audience_type=AUDIENCE,
    )
    assert len(clusters) == 740

    def sort_key(c):
        layer = c["pareto_layer"]
        bal = c["balance_score"]
        # pareto_layer ASC NULLS LAST, then balance_score DESC NULLS LAST.
        return (
            (1, 0) if layer is None else (0, layer),
            (1, 0.0) if bal is None else (0, -bal),
        )

    keys = [sort_key(c) for c in clusters]
    assert keys == sorted(keys), "rows not sorted by pareto_layer ASC, balance_score DESC (NULLS LAST)"

    first_layered = next((c for c in clusters if c["pareto_layer"] is not None), None)
    assert first_layered is not None
    assert first_layered["pareto_layer"] == 1
