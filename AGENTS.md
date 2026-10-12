# AGENTS.md

本文件是所有 AI 代理在本仓库工作的总规范。默认用中文沟通，除非用户明确要求英文。目标是减少自由发挥，保护生产环境，并让每次改动都能被人类和后续代理接住。

## 启动顺序

1. 先读根目录 `README.md`、本文件和 `CLAUDE.md`。
2. 涉及架构、流水线或历史决策时，再读 `docs/reddit_analysis/01_design/system_design.md`、`docs/reddit_analysis/05_folder_structure.md` 和相关 runbook。
3. 继续前一次未完成工作时，先看 `git status --short`，不要覆盖用户或其他代理留下的改动。
4. 改代码前先定位真实入口、调用方和测试，不要凭文件名猜。

## 生产环境红线

- 未经用户明确授权，不得 SSH 到服务器、拉取生产仓库、重启 worker/API/nginx、触发 daily/monthly/report job。
- 未经用户明确授权，不得对生产数据库执行 migration、backfill、delete、truncate、drop 或大规模 update。
- 本地仓库是代码来源。生产服务器不能手工改源码；紧急 server diff 必须带回本地审查并提交。
- 生产 checkout 不得保存 repo-local `.env`。`DATABASE_URL` 只是 bootstrap；运行时配置和密钥来自 PostgreSQL `system_settings`。
- 不要把模型 ID、API key、relay URL、scheduler 时间写进代码、文档示例以外的配置文件或提交内容。

## 改动纪律

- 每个任务只做用户要求的事。不要顺手重构无关模块、升级依赖、重排格式或改 UI 文案。
- 功能修改、结构重构、格式化、测试补充应尽量分开提交或至少在说明中分清。
- 旧 migration 视为历史记录，不要编辑；schema 变化只能新增编号 SQL。
- 旧脚本入口和 API 路径默认兼容，包括：
  - `scripts/b_ops_1_daily.sh`
  - `scripts/b_ops_3_monthly.sh`
  - `python3 -m src.sources ...`
  - `python3 -m src.pain_extraction ...`
  - `/api/v2/...`
- 删除文件、删表字段、移除命令、改变默认调度，都必须先得到明确同意。

## 后端组织规则

### 三层架构（router → service → repository）
- **Router** (`backend/routers/`) 只处理 HTTP：参数解析、认证依赖、状态码、响应模型。目标每个路由 ≤15 行，零内联 SQL、零业务逻辑、零缓存。
- **Service** (`backend/services/`) 负责业务流程编排：调用 repository、后处理（格式转换、percentile rank、sparkline 组装）、TTL 缓存。不依赖 FastAPI request/response 对象。
- **Repository** (`backend/repositories/`) 拥有全部 SQL 和事务边界。一个 domain 一个文件（`runs.py`、`clusters.py`、`velocity.py`、`reports.py`），每个函数自行 `with get_conn()` 管理连接。返回值是普通 dict/tuple/dataclass——不抛 HTTP 异常（遗留的 `validate_cluster_run_id` 是技术债，新函数用返回值 + service 层转换）。

### 分层边界
- 新代码优先放在清晰领域边界内：sources/extraction/clustering/jobs/report/dashboard。
- Dashboard API 的 SQL 只许放在 `backend/repositories/` 下；领域模块（`src/sources/`、`src/clustering/`、`src/report_pipeline/`）可在自己内部管理 SQL，但 Dashboard 调用它们时应通过 service 层。
- 共享连接池用 `src/dashboard/db.py` 的 `get_conn()` / `guc_transaction()`；不要在不同模块里各自 `psycopg2.connect(DATABASE_URL)`。

### Repository 约定
- 每个函数独立管理连接（`with get_conn() as conn:`），不要接受外部传入的 cursor。
- 查询函数返回原始行（tuple / dict list），行转 dict 的映射逻辑放 service。
- `default_ids()` 这类被多领域复用的函数放 `repositories/runs.py`，禁止在 router 间复制粘贴。

### Service 约定
- TTL 缓存（进程内 dict）放 service 层，对 router 透明。
- 后处理逻辑（percentile rank、dimension map、sparkline 组装）放 service。
- Service 可以从 repository 和 scoring/snapshot 等工具模块导入，但不能从 router 导入。

### 领域模块规则
- `src/sources/runtime_config.py`：sources 域的 service+repository 融合体（validate + CRUD）。Dashboard 通过 `admin_sources.py` router 调它的 service 函数，不直接操作 DB。
- `src/pipeline_jobs.py`：jobs 域的 service+repository 融合体，被 `admin_ops.py` router 和 worker 进程共用。
- Runtime config 以 PostgreSQL 为准；环境变量只作 bootstrap 或老代码兼容兜底。
- pipeline job、source runtime config、system settings 这类共享状态只能通过统一 helper 读写。

### Adapter 开发规范
- 新建数据源 adapter：在 `src/sources/<name>_adapter.py` 继承 `BaseAdapter`，用 `@register_adapter` 装饰，`discover_adapters()` 自动发现。
- 必须设置类属性：`name`（唯一标识）、`display_name`（人类可读，Dashboard 展示）、`description`（一句话说明）、`capabilities`（四布尔能力声明）。
- Prompt 模块：在 `src/pain_extraction/prompts/<name>.py` 导出 `bundle_to_postwithcomments(bundle: EventBundle) -> PostWithComments`，`_ensure_builtin_sources()` 自动 pkgutil 扫描注册，**无需手动改 registry.py**。
- Config 行通过 Dashboard UI（Sources 面板）或 `update_source_config()`/`delete_source_config()` 管理。已注册 adapter 即使无 config 行也会自动出现在列表中（全 disabled）。

## 前端组织规则

### Feature 组织
- Dashboard 前端按 feature 拆：clusters、clusterDetail、reports、jobs、settings、auth。
- `features/*/index.ts` 是纯 re-export 桶文件，指向 `components/` 下的真实组件。当前真实文件仍在 `components/` 扁平放置——后续可按 feature 迁入独立目录，但不要强行把单 feature 组件拉出 `components/`。
- 新写的 feature 容器组件先放 `components/`（如 `SourceSettingsPanel.tsx`），经 `features/*/index.ts` 导出。

### `shared/` vs `lib/`
- `shared/`：**只放 2 个及以上 feature 复用的** UI/hook/lib/type。当前入驻：`format.ts`（3 feature）、`colors.ts`（2 feature）、`jobs.ts`（2 feature）。
- `lib/`：单 feature 使用的工具（当前：`score.ts`、`scoreDescriptions.ts`、`score.test.ts`——仅 clusters feature 用）。
- 跨 feature 是唯一标准——不以"以后可能会复用"为由提前搬迁。

### API client
- `api/client.ts`：通用 `getJSON`/`postJSON`/`putJSON` + 401 鉴权回调 + GET 缓存（`getCachedJSON`）。
- 资源 client 按领域拆分：`adminSources.ts`、`adminSettings.ts`、`clusters.ts`、`pipelineJobs.ts`、`reports.ts`、`runs.ts`。
- 所有资源 client 是薄封装（调用 `client.ts` 的通用函数），不直接 import `fetch`。
- 根 `api.ts` 是 barrel，把各资源 client + `client.ts` 的导出重新收拢，兼容旧的统一 import 路径。

### 大组件拆分
- 大型容器必须拆成可测试的小单元。案例：`SystemSettingsPanel.tsx`（442行管 4 类状态）→ 拆出 `SourceSettingsPanel.tsx`（仅管 sources + dailyPlan）+ 精简后的 `SystemSettingsPanel.tsx`（325行管 settings + scheduler）。
- 拆分时不改变 UI 行为（不改交互、不改样式），仅拆状态和渲染边界。
- 纯展示子组件要有独立测试（如 `SourceControls.test.tsx`）。

### 测试
- 组件交互测试用 vitest，放在 `components/` 或 `lib/` 同级。
- 测试 mock repository 方法或 API client，不 mock SQL 文本。
- 修改前端后至少跑 `npm --prefix src/dashboard/frontend run build`；涉及组件行为时跑对应 vitest。

## 验证和交付

- 交付前说明实际运行过的命令和结果；没跑的测试要明说原因。
- 后端常用检查：
  - `python3 -m pytest src/dashboard/tests src/report_pipeline/tests src/mcp_config_ui/tests`
  - `bash -n scripts/b_ops_1_daily.sh scripts/b_ops_3_monthly.sh`
  - `python3 scripts/run_sources.py --phase fetch --dry-run`
- 前端常用检查：
  - `npm --prefix src/dashboard/frontend test`
  - `npm --prefix src/dashboard/frontend run build`
- 如果测试失败，先判断是本次改动导致还是既有环境问题，并把证据写清楚。

## AI 行为约束

- 大改前先给计划；计划必须包含范围、兼容策略、测试和回滚思路。
- 不要“看起来更优雅”就引入新框架、新目录体系或抽象层；必须能解释它解决了当前什么维护问题。
- 不要用生产环境试错。能本地验证的先本地验证，不能本地验证的先说明风险和操作步骤。
- 不要把猜测写成事实。涉及线上状态、最新 job、worker、模型、价格、外部 API 时必须验证。
- 不要制造“示例文件”放进 `src/`。`src/` 下的文件都视为生产代码。
