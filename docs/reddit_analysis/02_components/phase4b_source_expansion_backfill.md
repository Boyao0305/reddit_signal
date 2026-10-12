# Phase 4B — 数据源扩充:平台广度 + 时间深度

> **v1.1 (2026-06-27) — implemented.** Notes: historical backfill 默认改成 fetch-only；completed fetch job 通过 `Queue projection` 创建独立 projection job；EC2 worker 拆成 pipeline / source-backfill / extraction lanes,不同任务族不再互相排队。

> **v1.0 (2026-06-26) — implemented.** Commits: `05a4b52`(social seeds), `88a8dc5`(backfill), `174cf77`(cancel), `89704ea`(extraction), `1792d1f`(delete). Backfill 全流程支持 plan/create/process/retry/cancel/delete,Dashboard 前端 500+ 行管理面板。

> **Why this exists:** 数据源从 Reddit 单一平台扩展到 6 个平台(Facebook/Twitter/YouTube/GitHub/TikTok/Instagram),新增统一的历史回填 pipeline 支持对任意 source/venue 的时间窗口批量抓取。

---

## 1. 平台广度:社区种子扩种

| 平台 | 新增 | 覆盖方向 |
|---|---|---|
| **Facebook** | +30 个群组 | SaaS 创始人(2)、SMB 运营(含芝加哥/LA/NJ/Maine/德州等 7 个区域社群)、无代码(4)、AI 工具/自动化(13) |
| **Twitter/X** | +40 个社区 | Build in Public/IndieHacker(8)、SMB&B2B(5)、AI Agent/工程(6)、Shopify/Product Hunt(4)、创作者/YouTube(3)等 |
| **YouTube** | 搜索池 1→5 条查询,fetch_limit 200→250 | CRM、自动化、支持运维、无代码等多方向并行 |
| **GitHub/TikTok/Instagram** | 5 个新平台适配器上线(seed disabled) | 按需开启 |

新增种子通过 `social_source_venues` 表管理,全部 marked candidate,Pinterest/Substack 等平台现有适配器保持不变。YouTube 通过 `source_runtime_config.query` 行(newline-delimited 查询池)驱动,不走 venue 表。

## 2. 时间深度:统一历史回填

### 能力

| 操作 | 说明 |
|---|---|
| **Plan** | 指定 source/venue + date_from/date_to + chunk_days → 预览 chunk 数、覆盖质量分布 |
| **Create fetch** | 批量建 fetch-only `source_backfill_jobs` + `source_backfill_chunks`,500+ 行前端 `SourceBackfillModal` 管理 |
| **Process fetch** | source-backfill worker 逐 chunk 调 `adapter.fetch`,只写 L1/raw |
| **Queue projection** | completed fetch job 手动触发,复制成功窗口创建独立 `mode='project'` job |
| **Process projection** | projection job 调 `adapter.project`,把 L1/raw 投影到 `signal_events` |
| **Query/Detail** | 按 job 查进度、chunk 状态、rows_fetched/projected/extracted |
| **Retry** | 失败 chunks 单独重试 |
| **Cancel** | 安全停止运行中作业(job + 未开始 chunk 状态更新) |
| **Delete** | 清理已完成/已取消历史作业 |
| **Extraction** | projection 完成后可在 Sources > Processing 单独触发 extract + score + embed |

### 2026-06-27 执行语义调整

历史回填不再默认在同一个 chunk 里做 fetch + project。原因是 Reddit 这类大窗口抓取会产生大量 raw posts/comments,如果同进程立刻 project,单个 chunk 的停机响应和可观测性都会变差。

现在的默认链路是:

```text
Fetch job
  -> 只抓取 raw/source-native 数据
  -> job completed
  -> 用户点击 Queue projection
Projection job
  -> 只执行 adapter.project()
  -> 写入 signal_events
  -> 用户再从 Sources > Processing 触发 extraction + score + embed
```

Projection chunk 的粒度也做了去重:

- Reddit:按 `subreddit × window` 创建 projection chunk,保持精确窗口语义。
- 非 Reddit:按 `source × window` 创建 projection chunk,避免每个 venue 重复执行同一个 `adapter.project(since, until)`。

### 各平台能力差异(诚实标定)

| 平台 | 覆盖质量 | API 是否按日期过滤 | 回填效果 |
|---|---|---|---|
| **Reddit** | `full_window` | ✅ Arctic Shift 原生 after/before | 精准时间窗口回填 |
| **YouTube** | `best_effort_window` | ⚠️ 仅粗粒度(uploadDate: today/week/month/year) | API 粗筛 + project 按 event_time 精确过滤 |
| **Facebook** | `best_effort_window` | ❌ API 只接受群组 ID,忽略 since/until | API 返回最近帖子,project 按 event_time 过滤归位 |
| **Twitter/X** | `recent_only` | ❌ API 只接受社区 URL | 同上,历史窗口标注为"近期样本" |
| **HackerNews** | `recent_only` | ❌ Firebase 只暴露最近排行 | 同上 |

### 非 Reddit 平台的回填原理(两级过滤)

根因:`socialcrawl_base.py:148-155` — 基类 `_search_params` 直接丢弃 `since/until`,只传 `{"query": query}`。仅 YouTube 覆盖此方法加上 `uploadDate`。

```
Plan: 大时间区间切成 chunk_days 一 chunk,每 chunk 带 window_start/window_end
  ↓
Fetch: adapter.fetch(since, until, limit)
  → 对 Facebook/Twitter:since/until 被忽略,API 返回最近内容
  → 每条内容携带真实的 published_at/created_at → socialcrawl_items.event_time
  → 这里只写 raw/source-native 数据,不做 project
  ↓
Queue projection:用户在 completed fetch job 上手动创建 projection job
  ↓
Project job: adapter.project(since, until)
  → SQL: WHERE event_time BETWEEN window_start AND window_end
  → 按发布时间精准归位到对应时间窗口
```

**一句话:** 不是"让 API 返回历史数据",而是"反复多抓,每条内容自报发布时间,project 按时间精准归位"。

**前置条件:** 如果某历史时间段从未被 fetch 过,backfill 拉不回来。适合**建立持续抓取习惯后做存量补齐**,不适合"一次性把三年前数据全拉回来"。

### 覆盖质量标记

每个 chunk 在 `source_backfill_chunks` 表里存 `coverage_mode` 和 `coverage_note`,诚实标记该条数据的质量等级:

- `full_window` — Reddit,精准时间窗口
- `best_effort_window` — Facebook/YouTube,project 过滤,API 不保证
- `recent_only` — Twitter/HackerNews,仅为最近样本
- `unsupported` — 处理时直接 skipped

---

## 3. Schema 变更(migration v2_043)

- `source_backfill_jobs` — 回填作业表(sources, scope, mode, date_from/to, chunk_days, status, 总量/进度计数器)
- `source_backfill_chunks` — 分块表(job_id, source, target, query, window_start/end, coverage_mode, coverage_note, mode, status, rows_*)
- `social_source_venues` 新增 55 个 venue 种子(Facebook 25 + Twitter 30, `candidate` 状态) + 暂停验证组
- `source_backfill_chunks` 加 trigger 自动更新 updated_at
- `v2_046_source_backfill_fetch_only_default.sql` — 将 source backfill 默认 mode 改成 `fetch`,让 projection 显式排队执行。

## 4. CLI

`scripts/source_backfill.py` — plan/create/process/status/retry-failed/cancel/delete/queue-project 子命令。Dashboard 按钮使用更面向用户的 `Queue projection` 文案,CLI 保留技术名 `queue-project`。`scripts/evaluate_social_venues.py` — 批量评估 candidate venue 质量。

## 5. 与既有系统关系

- 每个 chunk 的 fetch 调用的仍是现有 source adapter(`SocialCrawlSourceAdapter.fetch`),不重复实现
- project 调用的仍是 `adapter.project`,利用已有 `socialcrawl_items` → `signal_events` 投影
- extraction 通过 pipeline_jobs 的 extraction 类型触发,现在由独立 `ec2_pipeline_worker.py --lane extraction` 消费
- daily/monthly 由 `--lane pipeline` 消费,source backfill fetch/projection chunk 由 `--lane source-backfill` 消费；不同 lane 互不阻塞,同一 lane 内仍按队列顺序执行

## 6. 测试

source backfill 相关测试覆盖:backfill job CRUD、chunk 状态机、cancel/delete 安全校验、后端 API 正常/异常路径。
