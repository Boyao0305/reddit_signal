# 0002 — Run on the existing Contabo server, without Zeabur

Date: 2026-10-10 · Status: accepted

## Context

Production lived on a Contabo VPS (`94.72.121.103`, 17 GB RAM, 1 TB disk) under Zeabur's k3s, plus
systemd workers outside it. The Zeabur web image was deleted from Zeabur's registry (~80 days of downtime
unnoticed). On 2026-10-09 k3s was stopped and disabled during the GTM project's move, taking the database
down. A Hetzner server was considered and rejected: 61 GB free disk and 7.6 GB RAM shared with an agent
setup; adding a volume costs extra. The owner does not want extra spend.

## Decision

- Stay on the Contabo server. Do not restart k3s.
- PostgreSQL runs in Docker (`pgvector/pgvector:pg18-bookworm`) from a verified cold copy of the old data
  directory at `/srv/reddit_signal/pgdata`, bound to `127.0.0.1:5433`. The Debian 12 (bookworm) build is
  pinned because the data directory was created under glibc 2.36; a different glibc can silently corrupt
  text indexes.
- All services move to Docker Compose under `/srv/reddit_signal/`, renamed from `openclaw` to
  `reddit_signal` (Linux user, paths, database, role — the role gets a new password).
- Nothing listens publicly except nginx on 80/443.
- The leftover logical-replication subscription from the old AWS database is disabled.

## Consequences

- One server holds the only copy of the data until an off-server backup exists; that backup is a
  prerequisite before deleting the original k3s data folder.
- nginx on 80/443 is shared with the GTM project until it finishes moving (ADR 0005).
- Memory is tighter than before for the monthly job only when other tenants are added; jobs get memory
  limits so overruns kill the job, not Postgres.

## Alternatives rejected

- **Hetzner + volume / bigger server / dedicated server** — extra monthly cost.
- **Restart k3s** — resurrects old Zeabur services (including GTM's) and fights GTM's nginx for 80/443.
- **Native PostgreSQL from apt** — different glibc than the data directory; collation risk.
