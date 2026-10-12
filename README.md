# OpenClaw LLM Analyse

这个仓库包含 Dashboard 2 和 Reddit 痛点分析流水线。当前生产部署是：

- 现有 Zeabur `web/API` 服务和 `llmanalyse.zeabur.app` 域名继续运行在 EC2 `54.82.99.49`。
- PostgreSQL 运行在目标主机的托管 `pgvector/pgvector:pg18` 容器中，生产库名为 `zeabur`。
- Web Pod 通过旧 EC2 上的受限 SSH 隧道访问目标 PostgreSQL，不直接使用无 TLS 的公网数据库端口。
- daily/monthly、source-backfill、extraction worker 作为目标主机 `openclaw` 用户的三个 systemd
  user service 运行，通过本机 `127.0.0.1:30237` 访问 PostgreSQL。

## Docker / Zeabur 预填环境变量

Docker/Zeabur 服务里只需要预先填写启动必需项：

```env
DATABASE_URL=postgresql://USER:PASSWORD@10.42.0.1:15432/zeabur?sslmode=disable
ZEABUR_SERVICE_MODE=web
PORT=8080
API_PORT=8765
MIGRATION_BOOTSTRAP_THROUGH=v2_022_pipeline_jobs.sql
```

说明：

- `DATABASE_URL` 必须留在容器环境变量里，因为应用要先连上 PostgreSQL，才能从数据库读取系统设置。
  `10.42.0.1:15432` 是旧 EC2 k3s Pod 到主机 SSH 隧道的本机入口，不是公网数据库地址。
- `PORT` 一般由 Zeabur 注入；保留 `8080` 默认值即可。
- `MIGRATION_BOOTSTRAP_THROUGH` 用于已有生产库的迁移账本初始化。当前生产 PostgreSQL 已经完成
  `v2_001` 到 `v2_022` 的结构变化，但 `schema_migrations` 账本是后来补的，所以容器第一次启动时会
  把 `v2_001..v2_022` 记录为已执行，然后只自动应用 `v2_023_dashboard_users.sql` 及之后的新迁移。
- `ADMIN_API_TOKEN` 现在不是前端 Settings 面板的必需项；后台接口会优先使用当前登录会话里的管理员权限。
  如果你还想保留无登录态的手工调试兜底，可以自行额外设置它。

## 自动数据库迁移

Zeabur `web` 容器启动时会先执行：

```bash
python scripts/migrate.py sync --bootstrap-through "$MIGRATION_BOOTSTRAP_THROUGH"
```

行为：

- 如果 `schema_migrations` 为空：先把 `v2_001..MIGRATION_BOOTSTRAP_THROUGH` 记为 baseline，不重跑旧 SQL。
- 然后按文件名顺序应用 baseline 之后还没记录的 migration。
- 迁移失败时容器会启动失败，避免 API 在缺表状态下继续返回 500。
- 当前登录表由 `migrations/v2_023_dashboard_users.sql` 创建；表为空时 API 会自动创建默认账号：
  `boyao / llmanalyse`。

## PostgreSQL System Settings

容器能连上 PostgreSQL 后，下面这些运行时配置应该存入 `system_settings` 表，并通过
Dashboard 右上角 `Settings` 面板修改：

```env
OPENROUTER_API_KEY=CHANGE_ME
OPENROUTER_MODEL=google/gemini-2.0-flash-lite-001
AUDIENCE_CLASSIFIER_MODEL=google/gemini-2.0-flash-lite-001
VIABILITY_MODEL=minimax/minimax-m2.7
ANTHROPIC_RELAY_BASE_URL=https://new-api-boyao.zeabur.app/v1/messages
ANTHROPIC_RELAY_API_KEY=CHANGE_ME
ANTHROPIC_RELAY_AUTH_HEADER=authorization
OPENCLAW_DAILY_UTC=04:05
OPENCLAW_MONTHLY_UTC=05:00
DASHBOARD_SNAPSHOT_REFRESH_ENABLED=true
DASHBOARD_SNAPSHOT_REFRESH_MINUTES=60
REDDIT_BACKFILL_WORKER_ENABLED=true
USE_SIGNAL_EVENTS=true
```

首次启用步骤：

```bash
python scripts/migrate.py apply
ZEABUR_SERVICE_MODE=settings-seed
```

`settings-seed` 会把当前环境变量写入 `system_settings`，但不会覆盖数据库里已经存在的值。
之后修改 OpenRouter key、Anthropic relay key/base URL、调度时间等，不需要重新部署；
新触发的 report、daily/monthly 会优先读取数据库里的配置。

## Web/API 与目标主机 Worker 通信

Zeabur web/API 不直接执行 daily/monthly。控制链路是：

```text
Settings 面板
  -> Zeabur FastAPI admin endpoint
  -> PostgreSQL pipeline_jobs
  -> 目标主机 ec2_pipeline_worker.py
  -> scripts/b_ops_1_daily.sh / scripts/b_ops_3_monthly.sh
```

必须先应用 `migrations/v2_022_pipeline_jobs.sql`，然后在目标主机上启动 worker：

```bash
cd /opt/openclaw/app
set -a
source /etc/openclaw/pipeline-worker.env
set +a
source /opt/openclaw/venv/bin/activate
PYTHONPATH=. python scripts/ec2_pipeline_worker.py --lane pipeline
PYTHONPATH=. python scripts/ec2_pipeline_worker.py --lane source-backfill
PYTHONPATH=. python scripts/ec2_pipeline_worker.py --lane extraction
```

生产使用三个 systemd user service 分别跑 `pipeline`、`source-backfill`、`extraction` lane；
`openclaw` 用户必须启用 systemd linger，确保服务不依赖 SSH 登录会话并能在主机重启后自动启动。
这样 daily/monthly、历史 fetch/projection、LLM extraction 互不阻塞；同一 lane 内仍按队列顺序执行。
如果需要保留定时 dashboard snapshot refresh,再加一个 `--lane snapshot` service；daily/monthly 成功后的
snapshot refresh 仍由 `pipeline` lane 触发。
旧 EC2 上对应的三个 worker 必须保持 disabled/stopped；旧 crontab 的 daily/monthly 两行继续保持注释，
避免重复执行。

## Reddit Subreddit 管理与扩容

Reddit 抓取范围现在由 PostgreSQL 表 `reddit_subreddit_sources` 管理，不再要求人工修改
Reddit source 的 `query` 字符串。

Dashboard 右上角进入 `Sources`，在 `Reddit subreddits` 区块可以：

- 新增或批量导入 subreddit，默认进入 `candidate`；
- 将 subreddit 设为 `active / paused / rejected`；
- 按 status 搜索，按 priority、pain yield、B2C contribution、assigned rate、pain objects 等指标排序；
- 手动 `Evaluate` 计算 posts/comments、signal_events、pain_objects、pain yield、assigned rate、B2C contribution；
- `Dry-run` 查看 18 个月 backfill 会拆成多少个 7 天 chunk；
- `Backfill` 写入 `reddit_backfill_jobs` / `reddit_backfill_chunks` 队列。

语义：

- `active`：进入 daily Reddit 抓取；
- `candidate`：保留在候选池，不自动进入 daily；
- `paused`：保留配置和历史数据，不继续抓取；
- `rejected`：保留人工判断，不抓取。

兼容规则：

- Reddit source 的 `query` 字段仍然保留为手动 override；只要它非空，adapter 会按 query 抓取。
- 生产建议把 Reddit source 的 `query` 留空，让 daily 使用 `reddit_subreddit_sources.status='active'`。
- `Preview daily plan` 会显示 `managed active subreddits (N)`，表示 daily 来自管理表。

Legacy Reddit-only Backfill 执行链路：

> 这是早期 subreddit 管理里的 Reddit-only 回填队列。新的全数据源 Historical fetch 使用
> `source_backfill_jobs` / `source_backfill_chunks`,默认 fetch-only,再手动 `Queue projection`。
> 详见 `docs/reddit_analysis/02_components/phase4b_source_expansion_backfill.md`。

```text
Sources 面板 Backfill
  -> /api/v2/admin/reddit-subreddits/{name}/backfill
  -> reddit_backfill_jobs / reddit_backfill_chunks
  -> EC2 ec2_pipeline_worker.py --lane legacy-reddit-backfill 执行 scripts/reddit_backfill_worker.py --limit 1
  -> Arctic Shift raw posts/comments
  -> job 全部 chunk 成功后 project / extract / assign / aggregate / snapshot refresh
```

默认每个 chunk 是 `subreddit × 7 days`，失败 chunk 会停在 `failed`，已经成功的 chunk 不需要重抓。
`REDDIT_BACKFILL_WORKER_ENABLED=false` 可以临时暂停 EC2 worker 消费 backfill 队列，不影响 daily/monthly
调度。

手动处理一个 queued chunk：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
set -a
source /home/ubuntu/.config/openclaw/pipeline-worker.env
set +a
source .venv/bin/activate
PYTHONPATH=. python scripts/reddit_backfill_worker.py --limit 1
```

只查看下一个 chunk，不改数据库：

```bash
PYTHONPATH=. python scripts/reddit_backfill_worker.py --dry-run
```

重跑失败 chunk：

```bash
# 重排某个 job 下全部 failed chunk
PYTHONPATH=. python scripts/reddit_backfill_worker.py --job-id 123 --retry-failed

# 只重排某一个 failed chunk
PYTHONPATH=. python scripts/reddit_backfill_worker.py --chunk-id 456 --retry-failed
```

## Cluster Data 日期筛选

Dashboard 列表顶部的 `Data from / Data to` 是 evidence window 筛选，按 pain object 的原始
`source_created` 时间过滤当前 `Cluster run` 内的成员，不会自动切换 `cluster_run_id`，也不会重新聚类。

行为：

- 日期筛选基于 `pain_clusters -> pain_cluster_members -> pain_objects.source_created`，不是
  `pain_volume_daily_subreddit`，因此 Reddit、Hacker News、SocialCrawl 等非 Reddit source 都会参与；
- 选择日期区间后，列表默认要求窗口内至少有 1 条 pain object，并按窗口内 pain count 排序；
- 手动从 `Cluster run` 下拉切换 run 时，日期窗口保持为用户当前选择，便于比较同一 evidence window；
- `analysis_runs.live_at` 只表示 run 进入 live 的时间，不再作为列表日期筛选依据。

## Cluster 证据窗口筛选接口

后端保留按帖子日期和 subreddit 过滤 evidence window 的接口能力。日期过滤是 source-agnostic 的；
`subreddits` 只是 Reddit surface 的附加过滤条件。它不会重新聚类，只影响窗口内 pain count 和
members quote。

接口参数示例：

```text
/api/v2/clusters?date_from=2026-05-01&date_to=2026-05-31&subreddits=ADHD,Anxiety&min_window_pain=5&sort=window_pain
/api/v2/clusters/{id}/members?date_from=2026-05-01&date_to=2026-05-31&subreddits=ADHD,Anxiety
/api/v2/cluster-filter-options?cluster_run_id=12
```

## Dashboard 列表快照

`/api/v2/clusters` 默认读取 `dashboard_market_opportunities` 快照表，不再每次实时重算
`v_market_opportunities_ranked`。快照的来源仍然是 ranked view，只是由 EC2 worker 后台刷新。

生成时机：

- 数据库迁移只创建快照表和刷新函数，不会自动生成快照数据。
- EC2 worker 启动后会立即尝试刷新所有可展示 run 的快照，之后按
  `DASHBOARD_SNAPSHOT_REFRESH_MINUTES` 定时刷新，默认 60 分钟。
- daily/monthly job 成功结束后，worker 会立即额外刷新所有可展示 run 的快照。
- 快照目标是所有 `analysis_runs` 里非 legacy、非 building/failed，且已有对应
  `viability_runs` 的组合；因此历史 run 下拉选项也会像 live run 一样预生成列表快照。
- 如果某个 run 组合还没有快照，API 会临时回落到实时 ranked view；这次请求仍可能较慢，但不会返回
  抽象的 503。

手动刷新当前生产组合示例：

```bash
PYTHONPATH=. python scripts/refresh_dashboard_snapshot.py \
  --cluster-run-id 10 \
  --viability-run-id 4 \
  --audience-classifier-version v1
```

也可以直接执行 SQL：

```sql
select * from refresh_dashboard_market_opportunities(10, 4, 'v1');
select * from dashboard_market_refresh_state order by updated_at desc limit 5;
```

## 短期爆发检测

Dashboard 的 `Burst` 信号固定使用 `3d vs prior 28d`：

- recent window：最近 3 天；
- baseline window：最近 3 天之前的 28 天；
- 先算 cluster 自身 lift，再除以 corpus lift，避免把全站大盘上涨误判成某个 cluster 爆发。

相关数据不会在列表接口里实时扫大表：

- `pain_volume_daily` 保存每个 cluster 的日级 pain count；
- daily 成功后增量刷新最近 32 天；
- monthly 成功后重建当前 run 的日级数据；
- `dashboard_market_opportunities` 快照里保存 `burst_score`、`burst_lift`、`burst_zscore`、
  `burst_recent_count`、`burst_baseline_count`、`burst_corpus_lift` 和 `burst_label`；
- `/api/v2/clusters?sort=burst` 只读快照，适合前端秒级排序。

## 通过面板管理定时任务

Dashboard 右上角进入 `Settings`：

1. 先用管理员账号登录。
2. 点击 `Settings`，面板会自动读取当前登录会话的权限。
3. 顶部 `Scheduler` 区块会显示：
   - EC2 worker 是否在线；
   - daily/monthly 当前 UTC 时间；
   - 下一次 daily/monthly 的北京时间。
4. 修改 `OPENCLAW_DAILY_UTC`、`OPENCLAW_MONTHLY_UTC` 或 dashboard snapshot 刷新配置后点击
   `Save changes`。
5. EC2 worker 最多约 1 分钟内重新读取 PostgreSQL 配置并重算下一次执行时间。
6. 需要立刻跑任务时，点击 `Run daily now` 或 `Run monthly now`。

注意：

- `Run daily now` / `Run monthly now` 只是向 `pipeline_jobs` 写入 queued job；真正执行进度看
  Settings 面板里的 recent jobs 或 EC2 worker 日志。
- 如果 worker 暂时离线，任务仍可排队；worker 恢复后会领取 queued job。
- `monthly` 会重跑聚类、标注、audience、quality、candidate gate、viability、weekly rebuild
  和 lineage，耗时和风险都比 daily 高，不建议随手触发。

## Docker 镜像体积说明

构建日志卡在 `exporting layers` / `sending tarball` 时，通常表示最终镜像层偏大，正在打包并上传
OCI tar。这个项目的主要体积来自 Python ML 依赖，尤其是 `sentence-transformers` 间接引入的
PyTorch、`scikit-learn`、`spacy`、`hdbscan` 等流水线依赖。

当前 `Dockerfile` 已经改成 Zeabur web/API 小镜像：

- 不再安装根目录 `requirements.txt`。
- 只安装 `requirements.web.txt`，避免把 `sentence-transformers`、PyTorch、HDBSCAN、UMAP、spaCy
  等 daily/monthly ML 依赖打进 Zeabur 镜像。
- 只复制 dashboard2、report pipeline、runtime settings、pipeline queue、migrations 和 Zeabur
  脚本。

daily/monthly 的大依赖暂时仍留在 EC2 worker 的现有虚拟环境里。后续如果要把 worker 也容器化，
建议单独做 worker 镜像或 ML base image。

## 本地生成文件

以下文件包含真实密钥，已经被 `.gitignore` 忽略，不能提交：

- `.env.zeabur.generated`：实际粘到 Zeabur Variables 的 Docker 预填项。
- `.env.system-settings.generated`：实际填到 Dashboard Settings 面板的运行时配置。
- `.env.server.snapshot`：从 EC2 复制出来的环境变量快照。

## 完整 Runbook

部署、数据库、公网 PostgreSQL、migration、旧 EC2 服务停用和回滚步骤见：

`docs/reddit_analysis/04_runbooks/dashboard2_deploy_and_cron.md`
