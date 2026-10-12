"""Operator CLI.

    python -m reddit_signal.cli status [--coverage]
    python -m reddit_signal.cli run daily | monthly [--force-promote] | extraction [--since --until --limit --workers]
    python -m reddit_signal.cli jobs list | show ID | cancel ID | recover
    python -m reddit_signal.cli backfill plan|create|process|status|retry-failed|queue-project ...
    python -m reddit_signal.cli subreddits list [--status S] | add NAME... [--status S] | set NAME --status S
    python -m reddit_signal.cli settings list | get KEY | set KEY VALUE
    python -m reddit_signal.cli migrate plan|apply|sync|baseline
    python -m reddit_signal.cli snapshot refresh [--all]
    python -m reddit_signal.cli promote RUN_ID [--force]

Commands that start work enqueue a job; the worker runs it. ``status`` reports live state (convention §14.3).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from reddit_signal import config


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _age(delta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _when(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


# --- status ----------------------------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    from reddit_signal import runtime_settings
    from reddit_signal.repositories import status

    now = datetime.now(timezone.utc)
    queue = status.jobs_by_status()
    print(f"queue      running={queue.get('running', 0)} queued={queue.get('queued', 0)} "
          f"cancel_requested={queue.get('cancel_requested', 0)}")
    for w in status.workers():
        stale = " STALE" if w["age"] > timedelta(minutes=5) else ""
        job = f" job={w['current_job_id']}" if w["current_job_id"] else ""
        print(f"worker     {w['worker_id']} seen {_age(w['age'])} ago{job}{stale}")
    for job_type in ("daily", "monthly", "extraction"):
        j = status.last_job_of_type(job_type)
        if j:
            err = f"  {j['error']}" if j["error"] else ""
            print(f"last {job_type:<9} #{j['id']} {j['status']} finished {_when(j['finished_at'])}{err}")
    run = status.live_run()
    if run:
        print(f"live run   #{run['id']} window {run['window_start']:%Y-%m-%d}..{run['window_end']:%Y-%m-%d} "
              f"clusters={run['cluster_count']} candidates={run['candidates']} live since {_when(run['live_at'])}")
    else:
        print("live run   none")
    dl = status.dead_letters()
    print(f"extraction dead letters: parked={dl['parked']} retrying={dl['retrying']}")
    if args.coverage:
        recency = int(runtime_settings.get_setting("EXTRACTION_RECENCY_DAYS") or 14)
        maturity = int(runtime_settings.get_setting("EXTRACTION_MATURITY_DAYS") or 2)
        since, until = now - timedelta(days=recency), now - timedelta(days=maturity)
        for source in ("reddit", "hackernews"):
            cov = status.extraction_coverage(source, since, until)
            pct = 100 * cov["done"] / cov["total"] if cov["total"] else 100.0
            flag = "  LOW" if cov["total"] and pct < 80 else ""
            print(f"coverage   {source:<10} posts {maturity}-{recency} days old: {cov['done']}/{cov['total']} "
                  f"extracted ({pct:.0f}%){flag}")
    print(f"settings   model={runtime_settings.get_setting('OPENROUTER_MODEL')} "
          f"providers={runtime_settings.get_setting('OPENROUTER_PROVIDER_ORDER') or 'default'} "
          f"daily_limit={runtime_settings.get_setting('DAILY_EXTRACTION_LIMIT')}")
    print(f"database   {status.database_size()}")
    return 0


# --- run / jobs -----------------------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    from reddit_signal import pipeline_jobs

    params: dict[str, Any] = {}
    if args.job_type == "monthly" and args.force_promote:
        params["force_promote"] = True
    if args.job_type == "extraction":
        if bool(args.since) != bool(args.until):
            print("--since and --until go together", file=sys.stderr)
            return 2
        if args.since:
            params.update(mode="backlog", since=args.since, until=args.until)
        if args.limit is not None:
            params["limit"] = args.limit
        if args.workers is not None:
            params["workers"] = args.workers
    job = pipeline_jobs.create_job(args.job_type, requested_by="cli", request_source="cli", params=params)
    print(f"queued {job.job_type} job #{job.id} {json.dumps(params) if params else ''}".rstrip())
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    from reddit_signal import pipeline_jobs

    if args.action == "list":
        for j in pipeline_jobs.latest_jobs(limit=args.limit):
            print(f"#{j.id:<5} {j.job_type:<10} {j.status:<16} created {_when(j.created_at)} "
                  f"finished {_when(j.finished_at)} {('- ' + j.error[:80]) if j.error else ''}")
        return 0
    if args.action == "show":
        job, steps = pipeline_jobs.get_job_with_steps(args.job_id)
        if job is None:
            print(f"job #{args.job_id} not found", file=sys.stderr)
            return 1
        _print_json({"job": job.to_dict(), "steps": [s.to_dict() for s in steps]})
        return 0
    if args.action == "cancel":
        job = pipeline_jobs.request_cancel(args.job_id, requested_by="cli")
        print(f"job #{job.id}: {job.status}")
        return 0
    if args.action == "recover":
        from reddit_signal.maintenance import recover_stale_jobs

        recovered = recover_stale_jobs(now=datetime.now(timezone.utc))
        print(f"marked failed: {recovered or 'none'}")
        return 0
    return 2


# --- backfill (ported from scripts/source_backfill.py) ------------------------------------------------------

def _sources(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").replace(" ", ",").split(",") if part.strip()]


def cmd_backfill(args: argparse.Namespace) -> int:
    from reddit_signal.sources import source_backfill
    from reddit_signal.sources.registry import discover_adapters

    discover_adapters()
    plan_kwargs = lambda: dict(sources=_sources(args.sources), date_from=args.date_from, date_to=args.date_to,  # noqa: E731
                               chunk_days=args.chunk_days, scope=args.scope, mode=args.mode, query=args.query)
    if args.action == "plan":
        _print_json(source_backfill.plan_backfill(**plan_kwargs()))
    elif args.action == "create":
        _print_json(source_backfill.create_backfill_job(**plan_kwargs(), requested_by="cli", dry_run=args.dry_run))
    elif args.action == "process":
        _print_json({"processed": source_backfill.process_chunks(job_id=args.job_id, limit=args.limit,
                                                                  dry_run=args.dry_run)})
    elif args.action == "status":
        if args.job_id:
            _print_json(source_backfill.get_job(args.job_id))
        else:
            _print_json({"jobs": source_backfill.list_jobs(status=args.status, limit=args.limit)})
    elif args.action == "retry-failed":
        _print_json({"requeued_chunks": source_backfill.retry_failed_chunks(job_id=args.job_id)})
    elif args.action == "queue-project":
        _print_json(source_backfill.create_project_job_from_fetch(job_id=args.job_id, requested_by="cli"))
    return 0


# --- subreddits / settings ---------------------------------------------------------------------------------

def cmd_subreddits(args: argparse.Namespace) -> int:
    from reddit_signal.sources import reddit_subreddits

    if args.action == "list":
        for s in reddit_subreddits.list_sources(status=args.status, limit=10_000):
            print(f"{s['subreddit_name']:<28} {s['status']:<10} priority={s.get('priority')}")
        return 0
    if args.action == "add":
        rows = reddit_subreddits.import_sources(args.names, status=args.status)
        print(f"added/updated {len(rows)}: {', '.join(r['subreddit_name'] for r in rows)}")
        return 0
    if args.action == "set":
        row = reddit_subreddits.upsert_source(args.name, {"status": args.status})
        print(f"{row.subreddit_name}: {row.status}")
        return 0
    return 2


def cmd_settings(args: argparse.Namespace) -> int:
    from reddit_signal import runtime_settings

    if args.action == "list":
        for row in runtime_settings.list_settings(mask_secrets=True):
            print(f"{row['key']:<36} {row['value']!s:<32} ({row['source']})")
        return 0
    if args.action == "get":
        spec = runtime_settings.SETTING_SPECS_BY_KEY.get(args.key)
        value = runtime_settings.get_setting(args.key)
        print("********" if spec and spec.is_secret and value else value)
        return 0
    if args.action == "set":
        runtime_settings.update_settings({args.key: args.value}, updated_by="cli")
        print(f"{args.key} updated")
        return 0
    return 2


# --- migrate / snapshot / promote -------------------------------------------------------------------------

def cmd_migrate(args: argparse.Namespace) -> int:
    from reddit_signal import migrate

    return migrate.main([args.action, *args.extra])


def cmd_snapshot(args: argparse.Namespace) -> int:
    from reddit_signal.maintenance import refresh_snapshots

    refresh_snapshots(reason="cli", all_targets=args.all)
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    from reddit_signal.core.promotion import decide_promotion
    from reddit_signal.repositories import analysis_runs

    decision = decide_promotion(
        analysis_runs.run_stats(args.run_id),
        analysis_runs.recent_promoted_stats(before_run_id=args.run_id),
        force=args.force,
    )
    for reason in decision.blocked_by:
        print(f"blocked: {reason}")
    for reason in decision.overridden:
        print(f"overridden by --force: {reason}")
    if not decision.promote:
        return 1
    analysis_runs.promote(args.run_id)
    print(f"run {args.run_id} is live")
    return 0


# --- argument parsing --------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m reddit_signal.cli", description="reddit_signal operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="queue, workers, last runs, live run, dead letters")
    p.add_argument("--coverage", action="store_true", help="also measure extraction coverage (slower)")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("run", help="enqueue a pipeline job")
    p.add_argument("job_type", choices=["daily", "monthly", "extraction"])
    p.add_argument("--force-promote", action="store_true", help="monthly: promote even if the size check fails")
    p.add_argument("--since", help="extraction backlog range start (ISO date/time, UTC)")
    p.add_argument("--until", help="extraction backlog range end (exclusive)")
    p.add_argument("--limit", type=int, help="extraction: max posts")
    p.add_argument("--workers", type=int, help="extraction: concurrent LLM calls")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("jobs", help="inspect or cancel jobs")
    jobs = p.add_subparsers(dest="action", required=True)
    j = jobs.add_parser("list")
    j.add_argument("--limit", type=int, default=15)
    for name in ("show", "cancel"):
        j = jobs.add_parser(name)
        j.add_argument("job_id", type=int)
    jobs.add_parser("recover", help="mark running jobs whose worker is gone as failed")
    p.set_defaults(fn=cmd_jobs)

    p = sub.add_parser("backfill", help="historical source backfill")
    bf = p.add_subparsers(dest="action", required=True)
    for name in ("plan", "create"):
        b = bf.add_parser(name)
        b.add_argument("--sources", required=True)
        b.add_argument("--date-from", required=True)
        b.add_argument("--date-to", required=True)
        b.add_argument("--chunk-days", type=int, default=7)
        b.add_argument("--scope", default="active")
        b.add_argument("--mode", default="fetch")
        b.add_argument("--query", default="")
        if name == "create":
            b.add_argument("--dry-run", action="store_true")
    b = bf.add_parser("process")
    b.add_argument("--job-id", type=int)
    b.add_argument("--limit", type=int, default=1)
    b.add_argument("--dry-run", action="store_true")
    b = bf.add_parser("status")
    b.add_argument("--job-id", type=int)
    b.add_argument("--status", default="all")
    b.add_argument("--limit", type=int, default=50)
    b = bf.add_parser("retry-failed")
    b.add_argument("--job-id", type=int)
    b = bf.add_parser("queue-project")
    b.add_argument("--job-id", type=int, required=True)
    p.set_defaults(fn=cmd_backfill)

    p = sub.add_parser("subreddits", help="manage the Reddit watchlist")
    sr = p.add_subparsers(dest="action", required=True)
    s = sr.add_parser("list")
    s.add_argument("--status", default="all")
    s = sr.add_parser("add")
    s.add_argument("names", nargs="+")
    s.add_argument("--status", default="candidate", choices=["active", "candidate", "paused", "rejected"])
    s = sr.add_parser("set")
    s.add_argument("name")
    s.add_argument("--status", required=True, choices=["active", "candidate", "paused", "rejected"])
    p.set_defaults(fn=cmd_subreddits)

    p = sub.add_parser("settings", help="runtime settings in system_settings")
    st = p.add_subparsers(dest="action", required=True)
    st.add_parser("list")
    s = st.add_parser("get")
    s.add_argument("key")
    s = st.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")
    p.set_defaults(fn=cmd_settings)

    p = sub.add_parser("migrate", help="database migrations")
    p.add_argument("action", choices=["plan", "apply", "sync", "baseline"])
    p.add_argument("extra", nargs=argparse.REMAINDER, help="passed to the migration runner")
    p.set_defaults(fn=cmd_migrate)

    p = sub.add_parser("snapshot", help="dashboard snapshot")
    sn = p.add_subparsers(dest="action", required=True)
    s = sn.add_parser("refresh")
    s.add_argument("--all", action="store_true", help="every run, not just the live one")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("promote", help="promote a built run (applies the promotion check)")
    p.add_argument("run_id", type=int)
    p.add_argument("--force", action="store_true", help="override the size check (not completeness)")
    p.set_defaults(fn=cmd_promote)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.configure(config.Config.from_env())
    config.get().require_database_url()
    return int(args.fn(args) or 0)
