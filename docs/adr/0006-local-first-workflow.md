# 0006 — Local-first development, server pulls a commit

Date: 2026-10-10 · Status: accepted

## Context

Previously code was copied to the server as plain files (`deployed_commit.txt`), production was patched
in place, and experiments ran against production. There was no CI.

## Decision

- Code is changed and tested locally, committed, and pushed to GitHub. The server checks out a specific
  commit and rebuilds with Docker Compose. No source edits on the server.
- Experiments and ad-hoc analysis use a **read-only** database role over an SSH tunnel.
- Tests for `core/` need no database; adapter tests use a local disposable Postgres (Docker Desktop) once
  available.
- Schema changes are new migration files only, applied locally first, then by the deploy.

## Consequences

- Every production change is a commit that can be reviewed and rolled back by checking out the previous
  commit.
- Contributors get the same workflow from the public repo.

## Alternatives rejected

- **Develop on the server.** Fast, but untraceable and the cause of past drift.
