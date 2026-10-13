"""
Step 4 CLI entry point.

Pipeline A — Ingestion (deterministic, append-only, daily cron):
  python -m reddit_signal.pain_extraction --ingest [--limit N] [--workers N]

Pipeline B — Analysis (parameterized, non-destructive, creates analysis_runs):
  python -m reddit_signal.pain_extraction --analyze --window 7d [--resolution daily]
  python -m reddit_signal.pain_extraction --analyze --window 30d
  python -m reddit_signal.pain_extraction --analyze --after 2026-03-01 --before 2026-04-06

Inspection:
  python -m reddit_signal.pain_extraction --runs                    # list all analysis runs
  python -m reddit_signal.pain_extraction --rank --run-id 5         # ranking for a specific run
  python -m reddit_signal.pain_extraction --inspect CLUSTER_ID      # cluster details (auto-detects run)

Individual steps (still work):
  python -m reddit_signal.pain_extraction --init-db
  python -m reddit_signal.pain_extraction --extract [--limit N] [--workers N]
  python -m reddit_signal.pain_extraction --score
  python -m reddit_signal.pain_extraction --embed
  python -m reddit_signal.pain_extraction --cluster [--min-cluster-size N] [--min-samples N]
  python -m reddit_signal.pain_extraction --rank [--top N]
  python -m reddit_signal.pain_extraction --label
  python -m reddit_signal.pain_extraction --stats
  python -m reddit_signal.pain_extraction --sample [--limit N]
  python -m reddit_signal.pain_extraction --all [--limit N] [--workers N]
  python -m reddit_signal.pain_extraction --pipeline
"""

import argparse
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import psycopg2.extras

from reddit_signal import config
from reddit_signal.core.extraction_policy import split_limit
from . import db, extraction, scoring
from .config import get_use_signal_events

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Thread-safe lock for DB writes during concurrent extraction
_db_lock = threading.Lock()


def _pipeline_job_id() -> int | None:
    return config.get().pipeline_job_id


def _job_event(
    *,
    message: str,
    event_type: str = "log",
    stage_name: str | None = "Extract + score + embed",
    details: dict | None = None,
) -> None:
    job_id = _pipeline_job_id()
    if job_id is None:
        return
    try:
        from reddit_signal import pipeline_jobs

        pipeline_jobs.add_job_event(
            job_id,
            message=message,
            event_type=event_type,
            stage_name=stage_name,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("failed to record pipeline job event: %s", exc)


# ─── Window parsing ───────────────────────────────────────────────────────────

def parse_window_args(window: str | None, after: str | None, before: str | None):
    """
    Resolve --window / --after / --before into (window_start, window_end).
    Defaults to --window 7d if nothing is specified.
    """
    now = datetime.now(tz=timezone.utc)

    if after or before:
        # Explicit date range
        ws = datetime.fromisoformat(after).replace(tzinfo=timezone.utc) if after else now - timedelta(days=365)
        we = datetime.fromisoformat(before).replace(tzinfo=timezone.utc) if before else now
        return ws, we

    # Parse Nd shorthand
    w = window or "7d"
    m = re.match(r"^(\d+)d$", w)
    if not m:
        raise ValueError(f"Invalid --window format '{w}'. Use Nd (e.g. 7d, 30d, 90d).")
    days = int(m.group(1))
    return now - timedelta(days=days), now


# ─── Sub-commands ─────────────────────────────────────────────────────────────

def cmd_init_db():
    print("Initialising Step 4 schema...")
    db.init_schema()
    # Create clusters table (idempotent)
    from . import clustering
    clustering.create_clusters_table()
    # Apply migrations (adds run_id FK etc.)
    db.migrate_schema()
    print("Done.")


def cmd_extract(limit: int | None, workers: int, source: str = "reddit"):
    extraction.smoke_check_model()
    use_signal_events = get_use_signal_events()
    if use_signal_events:
        print(f"[v2 path] Reading from signal_events (USE_SIGNAL_EVENTS=true)"
              f"{' (limit=' + str(limit) + ')' if limit else ''}...")
        # Pull in batches of 1000 until we exhaust unprocessed events or hit limit.
        posts: list = []
        offset = 0
        page = 1000
        while True:
            batch = db.get_event_bundles_batch_as_pwc(
                source, prompt_version="v1", offset=offset, limit=page,
                only_unprocessed=True,
            )
            if not batch:
                break
            posts.extend(batch)
            if limit and len(posts) >= limit:
                posts = posts[:limit]
                break
            offset += page
        print(f"Loaded {len(posts)} unprocessed posts via v2 path.")
    else:
        print(f"Fetching posts from DB{' (limit=' + str(limit) + ')' if limit else ''}...")
        posts = db.get_posts_with_comments(limit=limit)
        print(f"Loaded {len(posts)} posts.")

    if not posts:
        print("No posts found. Run step 3 first.")
        return

    # Skip posts already processed (resume support)
    processed_ids = db.get_processed_post_ids()
    unprocessed = [p for p in posts if p.post_db_id not in processed_ids]
    print(f"Already processed: {len(processed_ids)}. Remaining: {len(unprocessed)}.")

    if not unprocessed:
        print("All posts already extracted. Nothing to do.")
        return

    # Define the save callback (called per-post, thread-safe)
    def save_fn(pwc, pains):
        with _db_lock:
            for pain in pains:
                try:
                    db.upsert_pain_object(pain)
                except Exception as exc:
                    logger.warning("Failed to save pain for post %s: %s",
                                   pwc.post_reddit_id, exc)
            if source == "reddit":
                db.mark_posts_processed([pwc.post_db_id])
            elif use_signal_events and pwc.signal_event_id is not None:
                db.mark_signal_events_processed([pwc.signal_event_id], source=source)

    print(f"Starting concurrent extraction: {workers} workers, "
          f"model={extraction.get_openrouter_model()}")
    stats = extraction.extract_batch_concurrent(
        unprocessed,
        save_fn=save_fn,
        max_workers=workers,
        progress_every=100,
    )

    print(f"\nDone. Extracted {stats['extracted']} pain objects, "
          f"{stats['errors']} errors out of {stats['total']} posts.")


def cmd_score():
    print("Loading unscored pain objects...")
    unscored = db.get_unscored_pain_objects()
    print(f"Found {len(unscored)} unscored pain objects.")

    if not unscored:
        print("Nothing to score.")
        return

    print("Computing scores (VADER + spaCy)...")
    scored = scoring.score_pain_objects(unscored)

    print(f"Writing {len(scored)} scores to DB...")
    updated = 0
    for row in scored:
        try:
            db.update_pain_scores(row["id"], row["sentiment"],
                                  row["specificity"], row["community"])
            updated += 1
        except Exception as exc:
            logger.warning("Failed to update scores for id=%s: %s",
                           row["id"], exc)

    print(f"Updated {updated}/{len(scored)} pain objects with scores.")


def cmd_embed():
    from . import embedding
    embedding.embed_all_pain_objects()


def cmd_check_extraction_model():
    print(f"Checking OpenRouter extraction model: {extraction.get_openrouter_model()}")
    extraction.smoke_check_model()
    print("OpenRouter extraction model smoke check passed.")


def cmd_cluster(min_cluster_size: int, min_samples: int, deterministic: bool = False):
    from . import clustering
    clustering.cluster_all(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        deterministic=deterministic,
    )


def cmd_label(workers: int, run_id: int | None = None):
    from . import labeling
    labeling.label_all_clusters(max_workers=workers, run_id=run_id)


def cmd_rank(top_n: int, run_id: int | None = None):
    from . import ranking
    ranked = ranking.rank_clusters(run_id=run_id)
    ranking.print_ranking(ranked, top_n=top_n)


def cmd_inspect(cluster_id: int):
    from . import ranking
    details = ranking.get_cluster_details(cluster_id)
    if not details:
        print(f"Cluster {cluster_id} not found.")
        return

    run_info = f"  Run ID:      {details['run_id']}" if details.get("run_id") else ""
    print(f"\n{'═' * 70}")
    print(f"  CLUSTER #{details['id']}")
    print(f"{'═' * 70}")
    if run_info:
        print(run_info)
    print(f"  Members:     {details['member_count']}")
    print(f"  Intensity:   {details.get('intensity', '—')}")
    print(f"  Coherence:   {details.get('coherence', '—')}")
    print(f"  Comp. Dens.: {details.get('competitive_density', '—')}")
    print(f"  Opp. Score:  {details.get('opportunity_score', '—')}")
    print(f"\n  Label:\n  {details.get('label', '—')}")
    print(f"\n{'─' * 70}")
    print(f"  Top Members (by community score):")
    for i, m in enumerate(details.get("members", []), 1):
        tools = ", ".join(m["mentioned_tools"]) if m["mentioned_tools"] else "—"
        wk = "WORKAROUND" if m["is_workaround"] else ""
        print(f"\n  [{i}] r/{m['subreddit_name']} | u/{m['source_author']}")
        print(f"      {m['pain_statement'][:120]}")
        print(f"      Tools: {tools}  Sent: {m['sentiment_score']}  "
              f"Comm: {m['community_score']}  {wk}")
    print(f"{'═' * 70}\n")


def cmd_pipeline(min_cluster_size: int, min_samples: int, top_n: int,
                 deterministic: bool = False):
    """Run embed → cluster → rank in sequence (legacy)."""
    print("=" * 60)
    print("  STEP 4 ANALYSIS PIPELINE: Embed → Cluster → Rank")
    print("=" * 60)
    print()

    print("── Phase 1: Embedding ──")
    cmd_embed()
    print()

    print("── Phase 2: Clustering ──")
    cmd_cluster(min_cluster_size, min_samples, deterministic=deterministic)
    print()

    print("── Phase 3: Ranking ──")
    cmd_rank(top_n)
    print()

    print("Pipeline complete.")


# ─── Pipeline A: Ingestion ────────────────────────────────────────────────────

def cmd_ingest(
    limit: int | None,
    workers: int,
    after: "datetime | None" = None,
    sources: list[str] | None = None,
    limit_is_total: bool = False,
    window: "tuple[datetime, datetime] | None" = None,
):
    """
    Pipeline A: extract → score → embed (append-only, idempotent).
    Processes posts in batches to stay within memory limits.
    If `after` is given, only posts with created_utc >= after are processed (legacy path).
    If `window` is given, only parent events with event_time in [start, end) are processed,
    newest first (signal_events path; see core/extraction_policy.py).
    """
    print("=" * 60)
    print("  PIPELINE A: INGESTION (extract → score → embed)")
    if after:
        print(f"  Filter: posts.created_utc >= {after.isoformat()}")
    if window:
        print(f"  Window: event_time in [{window[0].isoformat()}, {window[1].isoformat()}), newest first")
    print("=" * 60)
    print()

    sources = sources or ["reddit"]
    print("── Phase 1: Extraction ──")
    extraction.smoke_check_model()
    remaining = limit
    source_limits = _compute_source_limits(
        sources=sources,
        limit=limit,
        limit_is_total=limit_is_total,
    )
    capped: list[str] = []
    for idx, source in enumerate(sources):
        source_limit = source_limits.get(source)
        if source_limit is not None and source_limit <= 0:
            _job_event(
                message=f"Extraction source {source} skipped because daily limit is exhausted",
                event_type="source_skipped",
                details={"source": source, "limit": limit},
            )
            print(f"  Source {source} skipped: daily extraction limit exhausted.")
            continue
        _job_event(
            message=f"Extraction source {source} started",
            event_type="source_start",
            details={"source": source, "limit": source_limit, "workers": workers},
        )
        result = _cmd_ingest_extract_source(
            source=source,
            limit=source_limit,
            workers=workers,
            after=after,
            window=window,
        )
        if limit_is_total and remaining is not None:
            processed = int(result.get("processed", 0))
            remaining = max(0, remaining - processed)
            unused = max(0, int(source_limit or 0) - processed) if source_limit is not None else 0
            if unused and idx + 1 < len(sources):
                tail = sources[idx + 1 :]
                share, extra = divmod(unused, len(tail))
                for tail_idx, tail_source in enumerate(tail):
                    source_limits[tail_source] = (source_limits.get(tail_source) or 0) + share
                    if tail_idx < extra:
                        source_limits[tail_source] += 1
            if source_limit is not None and processed >= source_limit:
                capped.append(source)

    # Budget a later source did not need (e.g. hackernews using 133 of its 5,000 while reddit stops at its
    # cap with far more waiting) goes back to the sources that hit their cap.
    if limit_is_total and remaining and capped:
        for source, extra_limit in split_limit(remaining, capped).items():
            if not extra_limit:
                continue
            _job_event(
                message=f"Extraction source {source} continues with unused budget",
                event_type="source_start",
                details={"source": source, "limit": extra_limit, "workers": workers, "pass": 2},
            )
            _cmd_ingest_extract_source(source=source, limit=extra_limit, workers=workers, after=after, window=window)

    print()

    print("── Phase 2: Scoring ──")
    cmd_score()
    print()

    print("── Phase 3: Embedding ──")
    cmd_embed()
    print()

    print("Ingestion complete.")


def _cmd_ingest_extract_source(
    *,
    source: str,
    limit: int | None,
    workers: int,
    after: "datetime | None" = None,
    window: "tuple[datetime, datetime] | None" = None,
) -> dict:
    use_signal_events = get_use_signal_events()
    # Each batch waits for its slowest call; ~10 posts per worker keeps the workers busy.
    batch_size = min(limit or 1000, max(200, min(1000, workers * 10)))
    last_id = 0
    total_extracted = 0
    total_errors = 0
    batch_num = 0
    posts_processed = 0
    _job_event(
        message=f"Source {source}: extraction configured",
        event_type="source_config",
        details={
            "source": source,
            "limit": limit,
            "workers": workers,
            "batch_size": batch_size,
            "use_signal_events": use_signal_events,
        },
    )

    cursor: dict = {}   # windowed runs continue after the previous batch instead of re-scanning it
    while True:
        batch_num += 1
        if use_signal_events:
            if after is not None:
                print("  WARNING: --after is ignored on USE_SIGNAL_EVENTS=true ingest path")
            posts = db.get_event_bundles_batch_as_pwc(
                source,
                prompt_version="v1",
                offset=0,
                limit=batch_size,
                only_unprocessed=True,
                window=window,
                cursor=cursor if window is not None else None,
            )
        else:
            if window is not None:
                raise RuntimeError("window-based extraction requires USE_SIGNAL_EVENTS=true")
            # get_unprocessed_posts_batch returns only posts without pain_objects
            posts = db.get_unprocessed_posts_batch(batch_size=batch_size, last_id=last_id, after=after)
        if not posts:
            _job_event(
                message=f"Source {source}: no unprocessed bundles remaining",
                event_type="source_empty",
                details={"source": source, "batches_seen": batch_num - 1},
            )
            break

        if use_signal_events:
            message = (
                f"Source {source}: batch {batch_num}: "
                f"next unprocessed signal_events ({len(posts)} bundles)"
            )
            print(f"  {message}")
            _job_event(
                message=message,
                event_type="source_batch",
                details={"source": source, "batch": batch_num, "bundles": len(posts)},
            )
        else:
            last_id = posts[-1].post_db_id
            print(f"  Batch {batch_num}: posts id>{last_id} ({len(posts)} posts)")

        def save_fn(pwc, pains):
            with _db_lock:
                saved = 0
                for pain in pains:
                    try:
                        if use_signal_events and pain.signal_event_id is None:
                            pain.signal_event_id = db.lookup_signal_event_id(
                                pain.post_id,
                                pain.comment_id,
                                source=source,
                                source_table=pwc.source_table,
                                source_row_id=pwc.source_row_id,
                            )
                        pain.prompt_version = "v1"
                        db.upsert_pain_object(pain)
                        saved += 1
                    except Exception as exc:
                        logger.warning("Failed to save pain for post %s: %s",
                                        pwc.post_reddit_id, exc)
                # Always mark as processed — a zero-pain post is a valid outcome,
                # not a retry condition. (Pre-fix bug: posts with no extracted pain
                # were re-attempted every daily run, ballooning OpenRouter cost.)
                if source == "reddit":
                    db.mark_posts_processed([pwc.post_db_id])
                elif use_signal_events and pwc.signal_event_id is not None:
                    db.mark_signal_events_processed(
                        [pwc.signal_event_id],
                        source=source,
                    )

        def fail_fn(pwc, error: str) -> None:
            if pwc.signal_event_id is None:
                return
            with _db_lock:
                db.record_extraction_failure(pwc.signal_event_id, error)

        stats = extraction.extract_batch_concurrent(
            posts,
            save_fn=save_fn,
            max_workers=workers,
            progress_every=50,
            fail_fn=fail_fn if use_signal_events else None,
        )
        total_extracted += stats["extracted"]
        total_errors += stats["errors"]
        posts_processed += stats["total"]
        _job_event(
            message=(
                f"Source {source}: batch {batch_num} complete: "
                f"{stats['extracted']} pains, {stats['errors']} errors, "
                f"{posts_processed} processed this run"
            ),
            event_type="progress",
            details={
                "source": source,
                "batch": batch_num,
                "batch_total": stats["total"],
                "batch_extracted": stats["extracted"],
                "batch_errors": stats["errors"],
                "run_processed": posts_processed,
                "run_extracted": total_extracted,
                "run_errors": total_errors,
                "limit": limit,
            },
        )

        if stats["errors"] == stats["total"]:
            raise RuntimeError(
                "Extraction batch made no progress; aborting to avoid retrying "
                "the same failing posts indefinitely."
            )

        if limit:
            if posts_processed >= limit:
                break

    print(
        f"  Source {source} done: {total_extracted} pains extracted, "
        f"{total_errors} errors."
    )
    _job_event(
        message=(
            f"Source {source} done: {total_extracted} pains extracted, "
            f"{total_errors} errors, {posts_processed} bundles processed."
        ),
        event_type="source_done",
        details={
            "source": source,
            "processed": posts_processed,
            "extracted": total_extracted,
            "errors": total_errors,
            "limit": limit,
        },
    )
    return {
        "processed": posts_processed,
        "extracted": total_extracted,
        "errors": total_errors,
    }


def _compute_source_limits(
    *,
    sources: list[str],
    limit: int | None,
    limit_is_total: bool,
) -> dict[str, int | None]:
    if not limit_is_total or limit is None:
        return {source: limit for source in sources}
    if limit <= 0 or not sources:
        return {source: 0 for source in sources}
    base, extra = divmod(limit, len(sources))
    return {
        source: base + (1 if idx < extra else 0)
        for idx, source in enumerate(sources)
    }


# ─── Pipeline B: Analysis ─────────────────────────────────────────────────────

def cmd_analyze(
    window: str | None,
    after: str | None,
    before: str | None,
    resolution: str,
    min_cluster_size: int,
    min_samples: int,
    top_n: int,
    workers: int,
    deterministic: bool = False,
):
    """
    Pipeline B: cluster → rank → label for a time window.
    Creates a new analysis_runs row; never deletes existing clusters.
    """
    window_start, window_end = parse_window_args(window, after, before)

    print("=" * 60)
    print("  PIPELINE B: ANALYSIS (cluster → rank → label)")
    print(f"  Window: {window_start.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"       → {window_end.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Resolution: {resolution}")
    print("=" * 60)
    print()

    # Create run record
    run_id = db.create_analysis_run(
        window_start=window_start,
        window_end=window_end,
        resolution=resolution,
        params={"window": window, "after": after, "before": before,
                "min_cluster_size": min_cluster_size, "min_samples": min_samples},
    )
    print(f"Created analysis run #{run_id}")

    # Clustering (run-based)
    from . import clustering, labeling, ranking

    print("\n── Phase 1: Clustering ──")
    result = clustering.cluster_all(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        window_start=window_start,
        window_end=window_end,
        run_id=run_id,
        deterministic=deterministic,
    )

    db.update_analysis_run(
        run_id=run_id,
        cluster_count=result.get("clusters", 0),
        noise_count=result.get("noise", 0),
        total_points=result.get("total", 0),
    )

    if result.get("clusters", 0) == 0:
        print("No clusters found in this window. Try a wider window.")
        return

    # Labeling
    print("\n── Phase 2: Labeling ──")
    labeling.label_all_clusters(run_id=run_id, max_workers=workers)

    # Ranking
    print("\n── Phase 3: Ranking ──")
    ranked = ranking.rank_clusters(run_id=run_id)
    ranking.print_ranking(ranked, top_n=top_n)

    print(f"\nAnalysis run #{run_id} complete.")
    print(f"  Use --rank --run-id {run_id} to re-view ranking.")
    print(f"  Use --runs to list all analysis runs.")


# ─── Inspection commands ──────────────────────────────────────────────────────

def cmd_runs():
    """List all analysis runs."""
    runs = db.get_analysis_runs()
    if not runs:
        print("No analysis runs yet. Use --analyze to create one.")
        return

    print(f"\n{'═' * 90}")
    print(f"  ANALYSIS RUNS")
    print(f"{'═' * 90}")
    print(f"{'ID':>4}  {'Resolution':>10}  {'Window Start':>19}  {'Window End':>19}  "
          f"{'Clusters':>8}  {'Points':>7}  {'Created':>19}")
    print(f"{'─' * 90}")
    for r in runs:
        ws = str(r["window_start"])[:19] if r["window_start"] else "—"
        we = str(r["window_end"])[:19] if r["window_end"] else "—"
        ca = str(r["created_at"])[:19] if r["created_at"] else "—"
        print(
            f"{r['id']:>4}  "
            f"{r['resolution']:>10}  "
            f"{ws:>19}  "
            f"{we:>19}  "
            f"{str(r['cluster_count'] or '?'):>8}  "
            f"{str(r['total_points'] or '?'):>7}  "
            f"{ca:>19}"
        )
    print(f"{'═' * 90}\n")


def cmd_stats():
    stats = db.get_stats()
    print("\n── Step 4 Stats ──────────────────────────────────────────────")
    print(f"  Total pain objects : {stats['total_pain_objects']}")
    print(f"  Scored             : {stats['scored']}")
    print(f"  Posts processed    : {stats['posts_processed']}")
    print(f"  Total posts in DB  : {stats.get('total_posts', '?')}")
    print("\n  Per subreddit:")
    for row in stats["per_subreddit"]:
        print(f"    r/{row['subreddit_name']:<30} "
              f"{row['pain_count']:>4} pains  "
              f"avg_sentiment={row['avg_sentiment']}  "
              f"workarounds={row['workarounds']}")
    print("─────────────────────────────────────────────────────────────\n")

    _print_analysis_stats()


def _print_analysis_stats():
    """Print embedding, clustering, and run stats."""
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
                    COUNT(*) FILTER (WHERE cluster_id IS NOT NULL) AS clustered,
                    COUNT(*) FILTER (WHERE cluster_id IS NULL AND embedding IS NOT NULL) AS noise,
                    COUNT(*) AS total
                FROM pain_objects
            """)
            r = dict(cur.fetchone())
            print(f"  Embedded           : {r['embedded']}/{r['total']}")
            print(f"  Clustered (legacy) : {r['clustered']}")
            print(f"  Noise (unclustered): {r['noise']}")

            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'pain_clusters'
                )
            """)
            if cur.fetchone()["exists"]:
                cur.execute("""
                    SELECT COUNT(*) AS cnt,
                           COUNT(*) FILTER (WHERE run_id IS NULL) AS legacy_cnt,
                           COUNT(*) FILTER (WHERE run_id IS NOT NULL) AS run_cnt,
                           AVG(coherence) AS avg_coh,
                           COUNT(*) FILTER (WHERE coherence >= 0.7) AS high_coh,
                           COUNT(*) FILTER (WHERE coherence >= 0.5 AND coherence < 0.7) AS med_coh,
                           COUNT(*) FILTER (WHERE coherence < 0.5) AS low_coh
                    FROM pain_clusters
                """)
                s = dict(cur.fetchone())
                print(f"  Total clusters     : {s['cnt']} "
                      f"(legacy: {s['legacy_cnt']}, run-based: {s['run_cnt']})")
                if s["avg_coh"]:
                    print(f"  Avg coherence      : {float(s['avg_coh']):.3f}")
                    print(f"  Coherence: HIGH(>=0.7): {s['high_coh']}  "
                          f"OK(0.5-0.7): {s['med_coh']}  "
                          f"LOW(<0.5): {s['low_coh']}")

            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'analysis_runs'
                )
            """)
            if cur.fetchone()["exists"]:
                cur.execute("SELECT COUNT(*) AS cnt FROM analysis_runs")
                run_count = cur.fetchone()["cnt"]
                print(f"  Analysis runs      : {run_count}")

            print("─────────────────────────────────────────────────────────────\n")
    finally:
        db.put_conn(conn)


def cmd_sample(limit: int):
    samples = db.get_sample_pain_objects(limit=limit)
    if not samples:
        print("No scored pain objects found yet.")
        return

    print(f"\n── Top {len(samples)} Pain Objects (by community score) ────────────")
    for i, s in enumerate(samples, 1):
        tools = ", ".join(s["mentioned_tools"]) if s["mentioned_tools"] else "—"
        print(f"\n[{i}] r/{s['subreddit_name']} | u/{s['source_author']}")
        print(f"    Pain: {s['pain_statement'][:120]}")
        print(f"    Tools: {tools}")
        print(f"    Workaround: {s['is_workaround']}  "
              f"Sentiment: {s['sentiment_score']}  "
              f"Specificity: {s['specificity']}  "
              f"Community: {s['community_score']}")
    print("─────────────────────────────────────────────────────────────\n")


def cmd_all(limit: int | None, workers: int):
    cmd_init_db()
    cmd_extract(limit=limit, workers=workers)
    cmd_score()
    cmd_stats()


# ─── CLI wiring ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Step 4: LLM Pain Extraction + Analysis Pipeline"
    )

    mode = parser.add_mutually_exclusive_group(required=True)

    # ── Pipeline commands ──
    mode.add_argument("--ingest", action="store_true",
                      help="Pipeline A: extract → score → embed (append-only)")
    mode.add_argument("--analyze", action="store_true",
                      help="Pipeline B: cluster → rank → label for a time window")
    mode.add_argument("--runs", action="store_true",
                      help="List all analysis runs")

    # ── Individual step commands (kept for backward compat) ──
    mode.add_argument("--init-db", action="store_true",
                      help="Create/migrate Step 4 schema")
    mode.add_argument("--extract", action="store_true",
                      help="Run LLM extraction (concurrent, resumable)")
    mode.add_argument("--check-extraction-model", action="store_true",
                      help="Fail-fast OpenRouter smoke check for the extraction model")
    mode.add_argument("--score", action="store_true",
                      help="Score unscored pain objects")
    mode.add_argument("--embed", action="store_true",
                      help="Embed all pain objects (sentence-transformers)")
    mode.add_argument("--cluster", action="store_true",
                      help="Run HDBSCAN clustering on embeddings (legacy destructive)")
    mode.add_argument("--rank", action="store_true",
                      help="Score and rank clusters by opportunity")
    mode.add_argument("--label", action="store_true",
                      help="Generate LLM labels for all clusters")
    mode.add_argument("--inspect", type=int, metavar="CLUSTER_ID",
                      help="Show details of a specific cluster")
    mode.add_argument("--pipeline", action="store_true",
                      help="Run embed → cluster → rank (legacy post-extraction)")
    mode.add_argument("--stats", action="store_true",
                      help="Print extraction + analysis stats")
    mode.add_argument("--sample", action="store_true",
                      help="Show top pain objects")
    mode.add_argument("--all", action="store_true",
                      help="Run init + extract + score + stats")

    # ── Common options ──
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only first N posts / show N samples")
    parser.add_argument("--workers", type=int, default=20,
                        help="Concurrent workers (default: 20)")
    parser.add_argument("--source", type=str, default="reddit",
                        help="Source to read from signal_events (default: reddit)")
    parser.add_argument("--all-enabled-sources", action="store_true", default=False,
                        help="Ingest every source with extract_enabled=true in source_runtime_config")
    # FIX-3: raised from 5→30 and 3→10 to filter noise with 200K+ pain objects.
    parser.add_argument("--min-cluster-size", type=int, default=30,
                        help="HDBSCAN min_cluster_size (default: 30)")
    parser.add_argument("--min-samples", type=int, default=10,
                        help="HDBSCAN min_samples (default: 10)")
    parser.add_argument("--top", type=int, default=30,
                        help="Show top N clusters in ranking (default: 30)")

    # ── Analysis window options ──
    parser.add_argument("--window", type=str, default=None,
                        help="Relative window: 7d, 14d, 30d, 60d, 90d (default: 7d)")
    parser.add_argument("--after", type=str, default=None,
                        help="Window start as ISO date (e.g. 2026-03-01)")
    parser.add_argument("--before", type=str, default=None,
                        help="Window end as ISO date (e.g. 2026-04-06)")
    parser.add_argument("--resolution", type=str, default="adhoc",
                        help="Run resolution label: adhoc, daily, weekly (default: adhoc)")
    parser.add_argument("--run-id", type=int, default=None,
                        help="Analysis run ID (used with --rank or --label)")
    # FIX-7: deterministic is now the default; add --non-deterministic to opt out.
    # --deterministic kept for backward compat (it's now a no-op since True is default).
    parser.add_argument("--non-deterministic", action="store_true", default=False,
                        help="Disable fixed random_state=42 in UMAP (enables parallelism, "
                             "sacrifices reproducibility)")
    parser.add_argument("--deterministic", action="store_true", default=False,
                        help="[no-op, kept for backward compat] UMAP is now deterministic by default")

    args = parser.parse_args()

    # FIX-7: deterministic=True by default; --non-deterministic opts out.
    deterministic = not args.non_deterministic

    if args.ingest:
        # Parse --after for B-prep.4 90d filter (and future incremental runs)
        after_dt = None
        if args.after:
            from datetime import datetime as _dt, timezone as _tz
            after_dt = _dt.strptime(args.after, "%Y-%m-%d").replace(tzinfo=_tz.utc)
        sources = [args.source]
        if args.all_enabled_sources:
            from reddit_signal.sources.runtime_config import enabled_sources_for_extract
            sources = enabled_sources_for_extract()
        cmd_ingest(
            limit=args.limit,
            workers=args.workers,
            after=after_dt,
            sources=sources,
            limit_is_total=args.all_enabled_sources,
        )
    elif args.analyze:
        cmd_analyze(
            window=args.window,
            after=args.after,
            before=args.before,
            resolution=args.resolution,
            min_cluster_size=args.min_cluster_size,
            min_samples=args.min_samples,
            top_n=args.top,
            workers=args.workers,
            deterministic=deterministic,
        )
    elif args.runs:
        cmd_runs()
    elif args.init_db:
        cmd_init_db()
    elif args.extract:
        cmd_extract(limit=args.limit, workers=args.workers, source=args.source)
    elif args.check_extraction_model:
        cmd_check_extraction_model()
    elif args.score:
        cmd_score()
    elif args.embed:
        cmd_embed()
    elif args.cluster:
        cmd_cluster(args.min_cluster_size, args.min_samples,
                    deterministic=deterministic)
    elif args.rank:
        cmd_rank(args.top, run_id=args.run_id)
    elif args.label:
        cmd_label(args.workers, run_id=args.run_id)
    elif args.inspect is not None:
        cmd_inspect(args.inspect)
    elif args.pipeline:
        cmd_pipeline(args.min_cluster_size, args.min_samples, args.top,
                     deterministic=deterministic)
    elif args.stats:
        cmd_stats()
    elif args.sample:
        cmd_sample(limit=args.limit or 10)
    elif args.all:
        cmd_all(limit=args.limit, workers=args.workers)


if __name__ == "__main__":
    main()
