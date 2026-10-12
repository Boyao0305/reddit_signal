# CLAUDE.md

本文件是 Claude / Claude Code 在本仓库工作的短规范。完整通用规则见 `AGENTS.md`，本文件只保留 Claude 代理最容易踩线的要求。

## 必读顺序

1. 先读 `AGENTS.md`。
2. 再读 `README.md`，确认当前部署形态：Zeabur web/API + EC2 worker + PostgreSQL queue。
3. 涉及架构或流水线时，读 `docs/reddit_analysis/01_design/system_design.md` 和 `docs/reddit_analysis/05_folder_structure.md`。
4. 继续旧任务时，先看 `git status --short`，不要覆盖用户或其他代理的改动。

## 工作方式

- 默认中文回复，代码、命令、API 名称保持原文。
- 大范围重构、数据迁移、生产操作前必须先给计划，不要直接执行。
- 实现时保持小步提交思维：先保兼容，再搬结构，再清旧代码。
- 不要自由发挥新增框架、依赖、目录体系或 UI 风格；必须沿用仓库现有技术栈。
- 不要把设计文档当源码导入，不要把“示例代码”放进 `src/`。

## Production Sync Rules

- 本地 `main` 是源码事实来源。生产服务器只拉取本地已确认的提交。
- 未经用户明确授权，不得 SSH 到 EC2、`git pull` 服务器、重启 worker/API/nginx、触发 daily/monthly/report。
- 不要在服务器上手工 patch 源码。如果发生 server-only diff，必须带回本地审查并提交。
- 生产 repo 不能有 `.env`。`DATABASE_URL` 是 bootstrap 变量；运行设置和密钥从 PostgreSQL `system_settings` 读取。
- 不要把 `OPENROUTER_MODEL`、`OPENROUTER_API_KEY`、`ANTHROPIC_RELAY_*`、`AUDIENCE_CLASSIFIER_MODEL`、`VIABILITY_MODEL` 或 scheduler 设置写进仓库文件。
- 需要部署时，先确认本地测试，再提交推送，再让服务器拉取指定提交，最后只重启必要服务并查日志。

## Backend Rules

- **Router** (`backend/routers/`): HTTP only — params, auth, status, delegate to service. Target ≤15 lines, zero SQL/cache.
- **Service** (`backend/services/`): orchestration, post-processing, TTL caches. No FastAPI dependencies.
- **Repository** (`backend/repositories/`): all SQL. One file per domain (`runs/clusters/velocity/reports`). Each function owns `with get_conn()`. Return plain dicts/tuples — no HTTP exceptions.
- Dashboard SQL must live under `backend/repositories/`; domain modules (`src/sources/`, `src/clustering/`) manage their own SQL.
- Shared connections via `src/dashboard/db.py::get_conn()` / `guc_transaction()`; never `psycopg2.connect(DATABASE_URL)` ad-hoc.
- `default_ids()` in `repositories/runs.py` is the canonical entry; never copy-paste it across routers.
- Old entry-points must stay compatible: `src.dashboard.api:app`, `python3 -m src.sources`, `python3 -m src.pain_extraction`, `scripts/b_ops_*.sh`.
- Migrations append-only; runtime config from PostgreSQL, env vars bootstrap/fallback only.
- **New adapters**: subclass `BaseAdapter` with `@register_adapter` in `src/sources/<name>_adapter.py`; set `name`/`display_name`/`description`/`capabilities`. Prompt module goes in `src/pain_extraction/prompts/<name>.py` exporting `bundle_to_postwithcomments` — auto-discovered, no registry edits needed.

## Frontend Rules

- **Features**: barrel `features/*/index.ts` re-export from `components/`. New feature containers go in `components/` first.
- **`shared/`**: 2+ feature usage only (`format.ts`, `colors.ts`, `jobs.ts`). Single-feature stays in `lib/`.
- **API client**: `api/client.ts` = generic fetch/cache/auth; resource clients (`adminSources.ts`, `clusters.ts`, …) are thin wrappers; root `api.ts` is barrel.
- **Large components must split**: extract state+logic into dedicated containers (e.g. `SourceSettingsPanel` from `SystemSettingsPanel`). Don't change UI behaviour during split.
- **Tests**: mock repository methods or API clients, never SQL-text matching. Run `npm build` + `npm test` (vitest) before committing frontend changes.

## Handoff

每次交付必须说清：

- 改了哪些模块。
- 保留了哪些兼容入口。
- 跑了哪些验证命令。
- 哪些测试没跑以及原因。
- 是否有生产操作；默认应该是没有。
