# Dashboard 2 部署、迁移和定时任务 Runbook

> Web/API：旧 EC2 `54.82.99.49` 上的 Zeabur service，域名 `llmanalyse.zeabur.app`
> Worker：目标主机 `openclaw` 用户 systemd，仓库 `/opt/openclaw/app`
> 数据库：目标主机托管 `pgvector/pgvector:pg18` 容器，库名 `zeabur`

## 当前生产拓扑（2026-07-13）

```text
llmanalyse.zeabur.app
  -> 旧 EC2 Zeabur web/API
  -> 10.42.0.1:15432 SSH tunnel
  -> 目标主机 127.0.0.1:30237
  -> PostgreSQL zeabur

目标主机 openclaw user systemd workers
  -> 127.0.0.1:30237
  -> pipeline_jobs / source_backfill_chunks
```

目标 PostgreSQL 公网 NodePort 不提供 TLS。Web/API 必须使用旧 EC2 上的
`openclaw-db-tunnel.service`，worker 必须使用目标主机本机 NodePort；不得把公网连接串写入 Web 或
worker 配置。目标数据库容器由平台托管，常规运维只使用 SQL 权限，不修改容器、PVC、HBA 或配置文件。

目标主机 worker（以 `openclaw` 用户登录后执行）：

```bash
systemctl --user status \
  openclaw-pipeline-worker.service \
  openclaw-source-backfill-worker.service \
  openclaw-extraction-worker.service

loginctl show-user openclaw -p Linger
```

三个 unit 必须为 `enabled/active`，且 `Linger=yes`，这样 worker 不依赖 SSH 会话并能在主机重启后
自动启动。运行状态同时可通过 Dashboard Scheduler 面板和 `pipeline_worker_heartbeats` 校验。

运行文件：

- 代码：`/opt/openclaw/app`
- Python：`/opt/openclaw/venv`
- bootstrap 环境：`/etc/openclaw/pipeline-worker.env`
- 日志：`/var/log/openclaw`
- 锁和 current run：`/var/lib/openclaw`

旧 EC2 上三个 user worker 必须保持 disabled/stopped，daily/monthly crontab 保持注释。Web 服务仍在旧
EC2，所以旧主机不能下线。

## 迁移前历史：Zeabur Web/API + EC2 Worker 过渡版

目标：先把 dashboard2 web/API 和 report pipeline 迁到 Zeabur Dockerfile 服务；daily/monthly
继续留在 EC2 执行，避免 Zeabur web 镜像被 `sentence-transformers` / PyTorch 等 ML 依赖拖大。

这版不再使用 `docker-compose.yml`，也不再保留单独的 `README.zeabur.md`。仓库根目录
提供唯一的 `Dockerfile`，Zeabur 直接使用这个 Dockerfile 构建。

不在本次停服范围：

- SSH
- PM1
- MCP / OpenClaw gateway
- Tailscale
- 服务器现有 Caddy
- k3s / Zeabur agent 组件

重要变化：Zeabur 容器不能继续使用 EC2 上的 `localhost` PostgreSQL。生产迁移到
Zeabur 时，`DATABASE_URL` 必须指向一个 Zeabur 能访问的 PostgreSQL。目前 EC2 已临时
开放 `5432`，所以第一阶段可让 Zeabur 连接 EC2 上的 `reddit_raw`。长期更建议迁到 RDS
或 Zeabur PostgreSQL，避免数据库长时间暴露在公网访问面上。

### 新运行面

第一阶段部署一个 Zeabur Web Service，建议服务名 `openclaw-web`。

`web` 模式在 Zeabur 容器内只启动两个进程：

- Nginx：监听 Zeabur 注入的 `${PORT}`，服务前端静态文件。
- FastAPI：监听容器内 `127.0.0.1:${API_PORT}`，只被 Nginx 反代访问。

请求路径：

- `/` 和前端静态资源由 Nginx 直接返回。
- `/api/*` 由 Nginx 反代到 FastAPI。
- `/healthz` 由 Nginx 反代到 FastAPI 健康检查。

daily/monthly 执行面继续留在 EC2：

- `scripts/ec2_pipeline_worker.py` 常驻运行。
- Zeabur API 手动触发时只写入 PostgreSQL `pipeline_jobs`。
- EC2 worker 轮询 `pipeline_jobs`，领取 `queued` job 后执行现有
  `scripts/b_ops_1_daily.sh` / `scripts/b_ops_3_monthly.sh`。
- EC2 worker 读取 PostgreSQL `system_settings` 中的 `OPENCLAW_DAILY_UTC` /
  `OPENCLAW_MONTHLY_UTC`，负责按时间自动 enqueue daily/monthly。

控制链路：

```text
Dashboard Settings
  -> Zeabur FastAPI admin endpoint
  -> PostgreSQL pipeline_jobs
  -> EC2 ec2_pipeline_worker.py
  -> b_ops_1_daily.sh / b_ops_3_monthly.sh
```

一次性运维任务可临时把同一镜像启动为：

- `ZEABUR_SERVICE_MODE=migrate-plan`
- `ZEABUR_SERVICE_MODE=migrate-baseline`
- `ZEABUR_SERVICE_MODE=migrate-apply`
- `ZEABUR_SERVICE_MODE=settings-seed`

### Dockerfile 行为

根目录 `Dockerfile` 做多阶段构建：

1. Node 阶段构建 `src/dashboard/frontend/dist`。
2. Python web build 阶段只把 `requirements.web.txt` 里的 web/API 依赖打成 wheel。
3. Python runtime 阶段只安装 web/API 依赖，复制前端 dist、dashboard、report pipeline、
   runtime settings、pipeline queue、migrations 和 Zeabur 脚本。

入口脚本：`scripts/zeabur/entrypoint.sh`。

默认模式由 Dockerfile 的 `CMD ["web"]` 提供。也可以显式设置：

```bash
ZEABUR_SERVICE_MODE=web
```

本地或服务器测试镜像：

```bash
docker build -t openclaw-llmanalyse:zeabur .
docker run --rm -p 8080:8080 --env-file .env \
  -e ZEABUR_SERVICE_MODE=web \
  -e PORT=8080 \
  openclaw-llmanalyse:zeabur
```

本地验证：

```bash
curl -sS http://127.0.0.1:8080/healthz
curl -sS http://127.0.0.1:8080/api/v2/runs | head -c 500
curl -sS "http://127.0.0.1:8080/api/v2/admin/scheduler-status?token=$ADMIN_API_TOKEN"
```

Admin API：

```bash
curl -X POST "https://<zeabur-domain>/api/v2/admin/trigger-daily?token=$ADMIN_API_TOKEN"
curl -X POST "https://<zeabur-domain>/api/v2/admin/trigger-monthly?token=$ADMIN_API_TOKEN"
```

构建日志如果长时间停在 `exporting layers` / `sending tarball`，通常表示最终镜像层偏大，正在打包并上传
OCI tar。当前 Zeabur 镜像不再安装根目录 `requirements.txt`，因此不会包含
`sentence-transformers`、PyTorch、`scikit-learn`、`spacy`、`hdbscan` 等 daily/monthly ML 依赖。
这些大依赖暂时保留在 EC2 worker 的现有虚拟环境里。

### Docker / Zeabur bootstrap 环境变量

参考 `.env.zeabur.example`，在 Zeabur 服务 Variables 中配置，不要提交真实密钥。

Docker/Zeabur 里只预填启动必需项：

```text
DATABASE_URL=postgresql://USER:PASSWORD@54.82.99.49:5432/reddit_raw?sslmode=require
ADMIN_API_TOKEN=...
ZEABUR_SERVICE_MODE=web
PORT=8080
API_PORT=8765
```

Zeabur Dashboard 配置：

```text
Service Type: Web Service
Deploy Method: Dockerfile
Root Directory: /
Dockerfile Path: Dockerfile
```

`DATABASE_URL` 是 bootstrap 变量，必须留在 Zeabur 环境变量里。`ADMIN_API_TOKEN` 也保留为
兜底门禁，避免 DB 设置异常时无法进入后台。

### PostgreSQL System Settings 变量

以下变量后续填到 dashboard 右上角 `Settings` 面板，存入 PostgreSQL `system_settings` 表：

```text
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
AUDIENCE_CLASSIFIER_MODEL=google/gemini-2.5-flash-lite
VIABILITY_MODEL=minimax/minimax-m2.7
ANTHROPIC_RELAY_BASE_URL=https://new-api-boyao.zeabur.app/v1/messages
ANTHROPIC_RELAY_API_KEY=...
ANTHROPIC_RELAY_AUTH_HEADER=authorization
OPENCLAW_DAILY_UTC=04:05
OPENCLAW_MONTHLY_UTC=05:00
DASHBOARD_SNAPSHOT_REFRESH_ENABLED=true
DASHBOARD_SNAPSHOT_REFRESH_MINUTES=60
USE_SIGNAL_EVENTS=true
```

首次启用 DB-backed settings：

```bash
# 先应用 v2_021_system_settings.sql
python scripts/migrate.py apply

# 再把当前 Zeabur 环境变量写入 system_settings；已有 DB 值不会被覆盖。
ZEABUR_SERVICE_MODE=settings-seed
```

之后修改 OpenRouter key、Anthropic relay key/base URL、worker scheduler 时间等，不需要重新部署。
保存后新触发的 report、daily/monthly 任务会读取新配置。

### Dashboard 列表快照

`/api/v2/clusters` 的列表页不再把每个请求都打到 `v_market_opportunities_ranked` 实时重算。
迁移 `v2_026_dashboard_market_snapshot.sql` 增加：

- `dashboard_market_opportunities`：Dashboard 列表快照表；
- `dashboard_market_refresh_state`：每个 `(cluster_run_id, viability_run_id, classifier_version)`
  的刷新状态；
- `refresh_dashboard_market_opportunities(run_id, viability_run_id, classifier_version)`：
  从 `v_market_opportunities_ranked` 生成快照的数据库函数。

快照生成时机：

- migration 只建表和函数，不自动生成数据；
- EC2 worker 启动后会立即尝试刷新所有可展示 run 的快照；
- 之后按 `DASHBOARD_SNAPSHOT_REFRESH_MINUTES` 刷新，默认每 60 分钟一次；
- daily/monthly job 成功结束后立即额外刷新所有可展示 run 的快照；
- 刷新目标是所有非 legacy、非 building/failed 且已有 `viability_runs` 的
  `(cluster_run_id, viability_run_id, classifier_version)` 组合，所以历史 run 下拉选项也会预生成快照；
- 如果某个 run 组合还没有快照，API 会临时回落到实时 `v_market_opportunities_ranked`，
  保证页面可用，但这次请求可能仍然慢。

手动刷新：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
set -a
source /home/ubuntu/.config/openclaw/pipeline-worker.env
set +a
source .venv/bin/activate
PYTHONPATH=. python scripts/refresh_dashboard_snapshot.py \
  --cluster-run-id 10 \
  --viability-run-id 4 \
  --audience-classifier-version v1
```

手动 SQL：

```sql
select * from refresh_dashboard_market_opportunities(10, 4, 'v1');
select * from dashboard_market_refresh_state order by updated_at desc limit 5;
select count(*) from dashboard_market_opportunities
where cluster_run_id = 10 and viability_run_id = 4 and audience_classifier_version = 'v1';
```

### EC2 Worker 启动

先应用 `v2_022_pipeline_jobs.sql`，再启动 EC2 worker。

一次性手动验证：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
git pull
set -a
source /home/ubuntu/.config/openclaw/pipeline-worker.env
set +a
source .venv/bin/activate
PYTHONPATH=. python scripts/ec2_pipeline_worker.py --lane pipeline --poll-seconds 10
PYTHONPATH=. python scripts/ec2_pipeline_worker.py --lane source-backfill --poll-seconds 10
PYTHONPATH=. python scripts/ec2_pipeline_worker.py --lane extraction --poll-seconds 10
# Optional dedicated hourly snapshot lane:
PYTHONPATH=. python scripts/ec2_pipeline_worker.py --lane snapshot --poll-seconds 10
```

建议拆成多个 systemd user service。不同 lane 互不阻塞；同一 lane 内仍按队列顺序执行。

`openclaw-pipeline-worker.service`：

```ini
[Unit]
Description=OpenClaw EC2 pipeline worker - pipeline lane
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/github/openclaw_boyao_1_llmanalyse
Environment=OPENCLAW_REPO=/home/ubuntu/github/openclaw_boyao_1_llmanalyse
Environment=OPENCLAW_LOG_DIR=/var/log/openclaw
Environment=PYTHONPATH=.
EnvironmentFile=/home/ubuntu/.config/openclaw/pipeline-worker.env
ExecStart=/home/ubuntu/github/openclaw_boyao_1_llmanalyse/.venv/bin/python scripts/ec2_pipeline_worker.py --lane pipeline --poll-seconds 10
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

`openclaw-source-backfill-worker.service`、`openclaw-extraction-worker.service`、`openclaw-snapshot-worker.service`
使用同样配置，只把 `Description` 和 `ExecStart --lane` 分别改成 `source-backfill`、`extraction`、`snapshot`。
`snapshot` lane 只负责按 `DASHBOARD_SNAPSHOT_REFRESH_*` 设置做小时级快照刷新；daily/monthly 成功后的快照刷新仍由
`pipeline` lane 触发。

启用：

```bash
mkdir -p ~/.config/systemd/user
$EDITOR ~/.config/systemd/user/openclaw-pipeline-worker.service
$EDITOR ~/.config/systemd/user/openclaw-source-backfill-worker.service
$EDITOR ~/.config/systemd/user/openclaw-extraction-worker.service
$EDITOR ~/.config/systemd/user/openclaw-snapshot-worker.service
systemctl --user daemon-reload
systemctl --user enable --now openclaw-pipeline-worker.service openclaw-source-backfill-worker.service openclaw-extraction-worker.service openclaw-snapshot-worker.service
systemctl --user status openclaw-pipeline-worker.service openclaw-source-backfill-worker.service openclaw-extraction-worker.service openclaw-snapshot-worker.service
```

worker 心跳正常后，注释旧 crontab daily/monthly 两行，避免 cron 和 worker scheduler 双跑：

```bash
crontab -l > /tmp/openclaw.cron.before_worker
crontab -l | sed \
  -e 's#^\(0 5 1 \* \*  /home/ubuntu/github/openclaw_boyao_1_llmanalyse/scripts/b_ops_3_monthly.sh.*\)$#\# \1#' \
  -e 's#^\(5 4 \* \* \*  /home/ubuntu/github/openclaw_boyao_1_llmanalyse/scripts/b_ops_1_daily.sh.*\)$#\# \1#' \
  | crontab -
```

### Dashboard 面板管理定时任务

进入 Dashboard 右上角 `Settings`：

1. 使用管理员账号登录。
2. 点击 `Settings`，面板会使用当前登录态读取配置。
3. 顶部 `Scheduler` 区块确认：
   - `Status` 是否为 `Worker running`；
   - daily/monthly 的 UTC 时间；
   - 下一次 daily/monthly 的北京时间。
4. 修改 `OPENCLAW_DAILY_UTC`、`OPENCLAW_MONTHLY_UTC`、`DASHBOARD_SNAPSHOT_REFRESH_ENABLED`
   或 `DASHBOARD_SNAPSHOT_REFRESH_MINUTES` 后点击 `Save changes`。
5. EC2 worker 最多约 1 分钟内重新读取 PostgreSQL 配置并重算下一次执行时间。
6. 需要立即执行时，点击 `Run daily now` 或 `Run monthly now`。

注意：

- 面板手动触发只负责写入 `pipeline_jobs`，执行进度和失败原因看 Settings 面板 recent jobs 或
  EC2 worker 日志。
- `Scheduler` 显示 `Worker offline` 时，任务仍可排队；worker 恢复后会领取 queued job。
- `monthly` 会重跑聚类、标注、audience、quality、candidate gate、viability、weekly rebuild 和
  lineage，耗时和影响范围都比 daily 大。

本地生成的实际值文件：

- `.env.zeabur.generated`：粘到 Zeabur Variables。
- `.env.system-settings.generated`：粘到 dashboard `Settings` 面板。

这两个文件都被 `.gitignore` 忽略，不能提交。

### 数据库迁移选择

当前 EC2 PostgreSQL 约 133GB。Zeabur 部署有三种数据库路径：

1. 推荐：新建 Zeabur PostgreSQL 或 RDS，把 EC2 数据迁过去。
2. 当前第一阶段：让 Zeabur 连接 EC2 PostgreSQL。已开放 `5432` 后，还需要确认 PostgreSQL
   的 `listen_addresses`、`pg_hba.conf`、数据库账号权限和 SSL 设置允许 Zeabur 连接。
3. 保守：先只把 dashboard web/API 上 Zeabur，daily/monthly 暂留 EC2，等数据库迁完再迁 worker。

当前服务器检查结果：

```bash
sudo ss -ltnp "sport = :5432"
sudo -u postgres psql -Atqc "show config_file; show hba_file; show listen_addresses; show ssl;"
```

截至 2026-06-16，PostgreSQL 已配置为可被 Zeabur 访问：`listen_addresses='*'`，并通过
`hostssl reddit_raw llmanalyse ... scram-sha-256` 做认证。长期仍建议把 `pg_hba.conf` 来源从
宽泛网段收窄到 Zeabur 固定出口 IP/CIDR，或者迁到 RDS / Zeabur PostgreSQL。

如果确认要让 Zeabur 临时直连 EC2 PostgreSQL，需要在服务器上做：

```bash
# 1. 备份配置
sudo cp /etc/postgresql/16/main/postgresql.conf \
  /home/ubuntu/deploy_backups/postgresql.conf.$(date -u +%Y%m%dT%H%M%SZ).bak
sudo cp /etc/postgresql/16/main/pg_hba.conf \
  /home/ubuntu/deploy_backups/pg_hba.conf.$(date -u +%Y%m%dT%H%M%SZ).bak

# 2. 让 PostgreSQL 监听公网/内网地址
sudo sed -i "s/^#\\?listen_addresses\\s*=.*/listen_addresses = '*'/" \
  /etc/postgresql/16/main/postgresql.conf

# 3. 允许 Zeabur 来源连接。优先用固定 Zeabur 出口 IP/CIDR 替换 <ZEABUR_CIDR>。
#    如果只能用 0.0.0.0/0，必须依赖强密码、SSL 和 Security Group 来源限制。
echo "hostssl reddit_raw llmanalyse <ZEABUR_CIDR> scram-sha-256" \
  | sudo tee -a /etc/postgresql/16/main/pg_hba.conf

# 4. 重启并验证
sudo systemctl restart postgresql@16-main
sudo ss -ltnp "sport = :5432"
sudo -u postgres psql -Atqc "show listen_addresses; show ssl;"
```

不建议长期使用 `0.0.0.0/0` 作为 `pg_hba.conf` 来源范围。更稳的长期路径仍是把
`reddit_raw` 迁到 RDS 或 Zeabur PostgreSQL。

如果迁到 Zeabur PostgreSQL，建议流程：

```bash
# EC2 上备份 schema 和数据。133GB 级别建议用 custom format，方便断点/并行恢复。
set -a
source /home/ubuntu/github/openclaw_boyao_1_llmanalyse/.env
set +a
pg_dump "$DATABASE_URL" -Fc -f /home/ubuntu/deploy_backups/reddit_raw_$(date -u +%Y%m%dT%H%M%SZ).dump

# 恢复到目标 DATABASE_URL，示例：
pg_restore --clean --if-exists --no-owner --no-acl \
  --dbname "$TARGET_DATABASE_URL" \
  /home/ubuntu/deploy_backups/reddit_raw_<timestamp>.dump
```

数据恢复后验证：

```bash
psql "$TARGET_DATABASE_URL" -c "select count(*) from analysis_runs;"
psql "$TARGET_DATABASE_URL" -c "select id,status,window_end,cluster_count from analysis_runs order by id desc limit 5;"
psql "$TARGET_DATABASE_URL" -c "select count(*) from v_market_opportunities_ranked;"
psql "$TARGET_DATABASE_URL" -c "select count(*) from reports;"
```

### Migration runner

新增 runner：`scripts/migrate.py`。

已有生产库之前是手工迁移，没有 `schema_migrations` 表。首次迁移前，如果已经确认目标库
schema 与 repo 的 migration 文件一致，先 baseline：

在 Zeabur 的 Run Command 或临时任务里执行：

```bash
python scripts/migrate.py baseline
```

之后看计划：

```bash
python scripts/migrate.py plan
```

以后新增 SQL 后再执行：

```bash
python scripts/migrate.py apply
```

注意：`v2_011_share_global_backfill.sql` 需要 `run_id` 参数。如果不是 baseline 状态，执行时需要传
`--var run_id=10`。Zeabur 临时任务不方便传参数时，优先 baseline 已经手工确认过的旧迁移。

### EC2 旧服务停用

Zeabur 服务验证通过后，再停 EC2 旧 dashboard2 和 cron。

备份：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
mkdir -p /home/ubuntu/deploy_backups

git status --short --branch > /home/ubuntu/deploy_backups/openclaw_status_$(date -u +%Y%m%dT%H%M%SZ).txt
git diff > /home/ubuntu/deploy_backups/openclaw_dirty_$(date -u +%Y%m%dT%H%M%SZ).patch
crontab -l > /home/ubuntu/deploy_backups/openclaw_crontab_$(date -u +%Y%m%dT%H%M%SZ).txt

systemctl --user cat openclaw-dashboard2-api.service \
  openclaw-dashboard2-frontend.service \
  > /home/ubuntu/deploy_backups/openclaw_dashboard2_units_$(date -u +%Y%m%dT%H%M%SZ).txt
```

停旧 dashboard2：

```bash
systemctl --user stop openclaw-dashboard2-api.service
systemctl --user stop openclaw-dashboard2-frontend.service
```

注释旧 cron：

```bash
crontab -l > /tmp/openclaw_cron.old
sed \
  -e 's#^\(0 5 1 \* \*  /home/ubuntu/github/openclaw_boyao_1_llmanalyse/scripts/b_ops_3_monthly.sh.*\)$#\# \1#' \
  -e 's#^\(5 4 \* \* \*  /home/ubuntu/github/openclaw_boyao_1_llmanalyse/scripts/b_ops_1_daily.sh.*\)$#\# \1#' \
  /tmp/openclaw_cron.old > /tmp/openclaw_cron.new
crontab /tmp/openclaw_cron.new
crontab -l
```

验证旧公网入口关闭：

```bash
curl -sS -m 10 http://54.82.99.49:5173/ | head -c 200
curl -sS -m 10 http://54.82.99.49:8765/healthz
```

预期：旧 EC2 `5173` 和 `8765` 不再提供 dashboard2。新的访问地址以 Zeabur 分配域名为准。

### 观察和回滚

Zeabur 观察：

- `openclaw-web` 日志应看到 Nginx access log 和 API 启动日志，不应再启动 daily/monthly scheduler。
- `GET /healthz` 返回 `{"ok": true}`。
- `/api/v2/runs` 返回 live run。
- cluster detail、velocity、report endpoints 可用。
- `/api/v2/admin/scheduler-status?token=...` 返回 `worker_running: true`，并带有 `workers` 和
  `recent_jobs`。
- 手动触发 daily/monthly 时，接口返回 `{"status":"queued"}`，`pipeline_jobs` 新增一条 queued job。

EC2 worker 观察：

- `systemctl --user status openclaw-pipeline-worker.service` 为 running。
- `journalctl --user -u openclaw-pipeline-worker.service -f` 能看到 worker heartbeat 和 job 执行日志。
- Settings 面板 `Recent pipeline jobs` 能看到 queued/running/succeeded/failed 状态变化。

EC2 回滚：

```bash
systemctl --user start openclaw-dashboard2-api.service
systemctl --user start openclaw-dashboard2-frontend.service
systemctl --user stop openclaw-pipeline-worker.service
crontab /home/ubuntu/deploy_backups/openclaw_crontab_<timestamp>.txt
```

如果数据库已经迁到 Zeabur/RDS，回滚时要把 EC2 `.env` 的 `DATABASE_URL` 指回目标库，或者明确使用迁移前的 EC2 本地库。

## 当前线上服务

Dashboard 2 使用 user-level systemd 服务：

```bash
systemctl --user status openclaw-dashboard2-api.service
systemctl --user status openclaw-dashboard2-frontend.service
```

服务定义：

- 后端：`openclaw-dashboard2-api.service`
  - 工作目录：`/home/ubuntu/github/openclaw_boyao_1_llmanalyse`
  - 启动命令：`/usr/bin/python3 -m uvicorn src.dashboard.api:app --host 0.0.0.0 --port 8765`
  - 环境文件：`/home/ubuntu/github/openclaw_boyao_1_llmanalyse/.env`
  - 日志：`/home/ubuntu/.hermes/logs/dashboard2-api.log`

- 前端：`openclaw-dashboard2-frontend.service`
  - 工作目录：`/home/ubuntu/github/openclaw_boyao_1_llmanalyse/src/dashboard/frontend`
  - 启动命令：`/usr/bin/npm run dev -- --host 0.0.0.0 --port 5173`
  - 日志：`/home/ubuntu/.hermes/logs/dashboard2-frontend.log`

注意：服务器上还有一个 `pm1-api.service` 监听 `127.0.0.1:8000`，它属于 `/home/ubuntu/github/pm1`，不是这个 repo 的 dashboard 后端。

## 拉取代码

服务器当前可能有未提交改动。部署前先备份 diff：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
mkdir -p /home/ubuntu/deploy_backups
git diff > /home/ubuntu/deploy_backups/openclaw_dirty_$(date -u +%Y%m%dT%H%M%SZ).patch
git status --short
```

确认本地改动不会和远端冲突后拉取：

```bash
git fetch origin
git merge --ff-only origin/main
```

如果 `--ff-only` 失败，不要强行 reset；先查看冲突原因。

## 数据库连接

EC2 服务器 `.env` 中的连接方式仍可使用本机 socket/TCP：

```text
DATABASE_URL=postgresql://llmanalyse:<password>@localhost/reddit_raw
```

Zeabur 需要使用公网可达 DSN，例如：

```text
DATABASE_URL=postgresql://llmanalyse:<password>@54.82.99.49:5432/reddit_raw?sslmode=require
```

如果只是从本地排查线上库，仍建议使用 SSH tunnel，避免本地工具直接打公网 PostgreSQL：

```bash
ssh -fN -i openclaw_boyao_1.pem \
  -L 127.0.0.1:15432:127.0.0.1:5432 \
  ubuntu@54.82.99.49
```

本地连接串：

```bash
DATABASE_URL=postgresql://llmanalyse:<password>@127.0.0.1:15432/reddit_raw
```

## 执行迁移

在服务器上执行：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
set -a
source /home/ubuntu/.config/openclaw/pipeline-worker.env
set +a

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/v2_017_analysis_runs_lifecycle.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/v2_018_run_aware_velocity_view.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/v2_019_dashboard_read_indexes.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/v2_020_stale_live_velocity_anchor.sql
```

验证 status 约束：

```bash
psql "$DATABASE_URL" -Atc \
  "SELECT pg_get_constraintdef(oid)
   FROM pg_constraint
   WHERE conname = 'analysis_runs_status_check';"
```

验证 dashboard 读路径索引：

```bash
psql "$DATABASE_URL" -Atc \
  "SELECT indexname
   FROM pg_indexes
   WHERE tablename IN ('pain_volume_weekly', 'pain_cluster_members')
   ORDER BY tablename, indexname;"
```

应看到：

```text
ix_pcm_cluster_pain
ix_pvw_run_week_cluster
```

验证 `Recent trend` 覆盖率：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  COUNT(*) FILTER (WHERE velocity_4w_ratio_normalized IS NOT NULL) AS recent_trend_non_null,
  COUNT(*) AS total_clusters,
  ROUND(
    100.0 * COUNT(*) FILTER (WHERE velocity_4w_ratio_normalized IS NOT NULL)
    / NULLIF(COUNT(*), 0),
    2
  ) AS pct_non_null
FROM v_cluster_velocity
WHERE cluster_run_id = 10;
SQL
```

背景：`analysis_runs.status='live'` 只表示 dashboard 默认展示该 run，不表示该 run 的数据窗口会持续滚到今天。run 10 的 `window_end` 是 `2026-05-06`；如果 velocity view 按当前周滚动，`velocity_4w_ratio_normalized` 会因为最近 4 周没有足够 assigned pain 而大量变成 `NULL`。`v2_020` 的规则是：只有 live run 的 `window_end` 覆盖当前周时才按当前周锚定；stale live run 按自己的 `window_end` 锚定。

## 重启 Dashboard 2

```bash
systemctl --user restart openclaw-dashboard2-api.service
systemctl --user restart openclaw-dashboard2-frontend.service

systemctl --user status openclaw-dashboard2-api.service --no-pager -l
systemctl --user status openclaw-dashboard2-frontend.service --no-pager -l
```

基础验证：

```bash
curl -sS -m 15 http://127.0.0.1:8765/api/v2/runs | head -c 500
curl -sS -m 15 "http://127.0.0.1:8765/api/v2/clusters/1/velocity?cluster_run_id=10" | head -c 500
curl -sS -m 10 http://127.0.0.1:5173/ | head -c 200
```

日志：

```bash
tail -n 200 /home/ubuntu/.hermes/logs/dashboard2-api.log
tail -n 200 /home/ubuntu/.hermes/logs/dashboard2-frontend.log
```

## 定时任务

当前 crontab：

```cron
0 5 1 * *  /home/ubuntu/github/openclaw_boyao_1_llmanalyse/scripts/b_ops_3_monthly.sh >> /var/log/openclaw/cron.log 2>&1
5 4 * * *  /home/ubuntu/github/openclaw_boyao_1_llmanalyse/scripts/b_ops_1_daily.sh >> /var/log/openclaw/cron.log 2>&1
```

服务器时区是 UTC：

- daily：每天 UTC 04:05，约北京时间 12:05。
- monthly：每月 1 日 UTC 05:00，约北京时间 13:00。

查看 crontab：

```bash
crontab -l
```

编辑 crontab：

```bash
crontab -e
```

查看 cron 总日志：

```bash
tail -n 300 /var/log/openclaw/cron.log
```

查看按日期拆分的流水线日志：

```bash
ls -lh /var/log/openclaw
tail -n 200 /var/log/openclaw/daily_$(date -u +%F).log
tail -n 200 /var/log/openclaw/monthly_$(date -u +%Y-%m-01).log
```

手动 dry-run daily：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
OPENCLAW_REPO=$PWD scripts/b_ops_1_daily.sh --dry-run --limit 1
```

手动 dry-run monthly：

```bash
cd /home/ubuntu/github/openclaw_boyao_1_llmanalyse
OPENCLAW_REPO=$PWD scripts/b_ops_3_monthly.sh --dry-run
```

## 2026-06-13 部署记录

本次已完成：

1. 服务器 dirty diff 已备份到：

   ```text
   /home/ubuntu/deploy_backups/openclaw_dirty_20260613T085651Z.patch
   ```

2. 服务器 repo 已 fast-forward 到：

   ```text
   58b2318
   ```

3. 已应用迁移：

   - `v2_017_analysis_runs_lifecycle.sql`
   - `v2_018_run_aware_velocity_view.sql`
   - `v2_019_dashboard_read_indexes.sql`
   - `v2_020_stale_live_velocity_anchor.sql`（后续补丁：修复 run 10 stale live 导致 Dimensions 里的 `Recent trend` 大量为空）

4. 已重启：

   - `openclaw-dashboard2-api.service`
   - `openclaw-dashboard2-frontend.service`

5. 已验证：

   - `/api/v2/runs` 可返回数据。
   - `/api/v2/clusters/1/velocity?cluster_run_id=10` 可返回数据。
   - `http://127.0.0.1:5173/` 可返回前端 HTML。

保留状态：

- 服务器仍有 4 个原本存在的未提交 `src/report_pipeline/*` 改动，未覆盖。
