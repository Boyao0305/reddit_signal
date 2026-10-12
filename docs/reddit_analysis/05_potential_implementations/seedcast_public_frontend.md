# Seedcast — public, read-only frontend

Status: frontend and backend implemented (2026-10-11). Backend: `routers/public.py`, `services/public.py`, `repositories/public.py`; display rules in `core/public_view.py`.

Seedcast is the customer-facing, no-paywall visualization of the pain clusters. It is a second Vite entry
(`src/dashboard/frontend/seedcast.html` → `src/seedcastMain.tsx`) in the existing frontend project. It reuses
React 18 + Tailwind; no new dependencies. Routing is hash-based (`#/`, `#/movers`, `#/cluster/:id`, `#/compare`).

## Pages

| Page | Route | What it shows |
|---|---|---|
| Landscape | `#/` | Packed bubbles (area = posts in last 4 weeks; color = momentum or audience), hover caption, scrollable list with Show more |
| Movers | `#/movers` | Top 10 as large trend cards (Rising / Fading / Bursting), then ranked list from #11 |
| Cluster | `#/cluster/:id` | Year trend (posts / share of all), stats, quotes, where, source mix, tools |
| Compare | `#/compare` | Search all clusters, overlay up to 4 (each scaled to own peak), side-by-side cards |

## Where things live

- Components + pages: `src/components/seedcast/` (barrel: `src/features/seedcast/index.ts`)
- Pure logic (tested): `src/lib/seedcast/` — `pack.ts` (circle packing), `rank.ts`, `paths.ts`, `format.ts`, `route.ts`
- Data contract: `src/lib/seedcast/types.ts`; fixtures: `src/lib/seedcast/fixtures.ts`
- API wrapper: `src/api/seedcast.ts` (calls the public endpoints; `VITE_SEEDCAST_FIXTURES=1` uses built-in sample data)
- Tokens: `tailwind.config.js` (`seed-*` colors, `font-seed`, `font-seedmono`)
- Dev: `npm run dev`, open `/seedcast.html`. The build output also contains `dist/seedcast.html`.

## Required public endpoints (not implemented)

Read-only, no auth, cached (match the 300 s TTL used by `/clusters`). Router <= 15 lines; SQL in
`backend/repositories/`; orchestration in `backend/services/`.

- `GET /api/public/snapshot` -> `SeedcastSnapshot` (all clusters of the live run in one response; the existing
  `/clusters` is auth-protected and capped at `page_size <= 50`).
- `GET /api/public/clusters/{id}` -> `SeedcastClusterDetail`.

## Field mapping (UI -> existing data)

| UI field | Source |
|---|---|
| `name` | `pain_clusters.label` (nullable: fall back to ICP hint or top member `pain_statement`) |
| `audience`, `icp` | `cluster_audience.audience_type`, `icp_hint` |
| `posts4w`, `total` | `pain_count_last_4w`, `member_count` |
| `ratio` | `velocity_4w_ratio` (NULL when either 4-week window has < 3 posts) |
| `burst`, `bursting` | `burst.lift` / `burst.label` |
| `communities`, `where` | `surface_distribution`, `cluster_quality_metrics.subreddit_count` |
| `workaroundPct` | `cluster_quality_metrics.workaround_ratio` |
| `series` | weekly `pain_volume_weekly` (last 26 weeks; last week partial) |
| `history`, `share` | `/clusters/{id}/velocity` (`pain_count`, `share_global`) |
| `quotes`, `tools`, `sources` | cluster members (`pain_statement`/`raw_quote`, `mentioned_tools`, `source`) |

Not exposed publicly: competition, viability and Pareto scores. Usernames must be stripped from quotes.

## Open items

- `statement`, `firstSeen` and `topCommunity` have no ready-made field; derive them in the service layer.
- Cluster IDs change at the monthly re-cluster; links should use lineage-stable IDs.
- Production serving of `seedcast.html` (Zeabur/nginx) is not configured or touched.

## Verification (local)

`npm run build` and `npm test` in `src/dashboard/frontend` (tsc + 66 vitest tests, 16 of them Seedcast).
