# 0003 — A CLI replaces the admin website; only a read-only API is public

Date: 2026-10-10 · Status: accepted

## Context

The old dashboard combined an analytics UI and admin actions (run daily/monthly, backfills, subreddit
management, settings). Its login check was bypassed in commit `a7c50ed`, so admin endpoints were open to
anyone who could reach the API. The owner uses the system rarely; it is a side project.

## Decision

- Deploy only the public Seedcast site and a read-only API (`/api/public/*`).
- Operator actions move to a `cli` container run over SSH: `status`, `run`, `jobs`, `backfill`,
  `subreddits`, `settings`, `migrate`. Commands enqueue work into the existing `pipeline_jobs` queue;
  the worker executes it.
- The admin dashboard code is deleted once the CLI covers its actions.
- For ad-hoc analysis, the owner runs tools locally against an SSH tunnel.

## Consequences

- No write path on the public internet.
- Convention §14.3 is satisfied: `cli status` reports live state (queue, workers, last runs, backlog, disk).

## Alternatives rejected

- **Keep the admin site behind a login.** More code to secure and maintain for rare use.
- **Admin site on a private port via tunnel only.** Keeps a second frontend alive for little benefit over
  a CLI.
