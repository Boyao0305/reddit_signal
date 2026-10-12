# Phase 4A — 竞争算法降本:领域批量 + SocialCrawl 交叉验证 + 成本可见

> **v1.0 (2026-06-26) — implemented.** 23 files changed, 9 new files, 685+ lines added. 107 pytest passed, vite+tsc build clean. Commit: `4418bfa`.

> **Why this exists:** 每月 Top-50 个 cluster 逐个调 sonar-pro 做竞品研究 = 50 次调用,成本不可见(`cost_usd = 0 stub`),竞品 satisfaction_gap 全靠 LLM 自己猜,无外部证据。

---

## 1. 目标

三条并行改进,一条流水线完成:

```
50 个 cluster
    ↓  ① embedding 自动聚组(纯算法,零外部调用)
~12 个领域
    ↓  ② 每领域 1 次 sonar-pro(只负责"发现这个领域有哪些竞品")
领域竞品池(去重 ~30 个)
    ↓  ③ SocialCrawl 逐竞品交叉验证(口碑 + 需求)
竞品 satisfaction_gap 回填 → 评分 → 存库
```

| 动作 | 之前 | 之后 | 效果 |
|---|---|---|---|
| ① 按领域批量 | 50 cluster × 1 次调用 = 50 次 | 自动聚 ~12 组,每组 1 次 = ~12 次 | 调用量降 76% |
| ② SocialCrawl 口碑验证 | satisfaction_gap 全靠 LLM 猜 | 跨 Trustpilot + App Store + Google + Web 加权评分回填 | 口碑有外部数据支撑 |
| ③ 成本可见 | cost_usd = 0,无人知 | token×单价算真实美元,Dashboard Cost 面板展示每 run/月/白做率/信心 | 老板随时看账 |

## 2. 领域分组算法(§`domain_grouping.py`)

纯算法,不调 LLM:

1. 从 `pain_clusters` 取 Top-N cluster 的 `centroid`(384 维 pgvector embedding)
2. 余弦相似度贪心分组:选最高优先级未分配 cluster 为种子,吸收相似度 ≥ 0.78 的 cluster
3. 每组选代表 cluster(label 最具代表性) + 汇总该组所有 `mentioned_tools` 为竞品种子
4. 分组数可配(`COMPETITION_DOMAIN_COUNT`),默认阈值产生 ~12 组

同领域 cluster **共享竞品池**,但每 cluster 用**自己的 mentioned_tools 重叠度**调整 supply gap——共享但不雷同。

## 3. SocialCrawl Prism 交叉验证(§`socialcrawl_client.py`)

复用已有 `SocialCrawlClient`(v2_031 接入,调 `SOCIALCRAWL_API_KEY`),新增 Prism 分析层封装:

| 端点 | 消耗 | 输入 | 覆盖源 |
|---|---|---|---|
| `reputation` | 30 cr | 产品名(如 Notion) | Trustpilot + App Store + Google Play + Google Business + Web |
| `demand-signals` | 30 cr | 产品名 | App 评论速率 + Web 提及斜率 + Reddit 讨论 + 电商评论 |

验证结果回填进 competitor 的 `satisfaction_gap` 和 `confidence=verified`,随后 `score_research_output` 使用验证过的值计算 `competition_intensity` / `external_supply_gap`。

默认验证 top-30 竞品(按 strength_score),预计 ~1,800 cr/run,Growth 包 2 万 cr 够 ~11 次 monthly。`--no-socialcrawl` CLI 开关 + `COMPETITION_SOCIALCRAWL_ENABLED` runtime setting 可随时关。

Sonar-pro 的职责简化为**仅发现竞品**(竞品名 + 分类),SocialCrawl 负责**验证竞品质量**(口碑 + 需求强度)。

## 4. 容灾

- 某领域 sonar-pro 失败 → 自动回退逐 cluster 单独调(`_research_and_save_cluster`)
- SocialCrawl 失败 → 竞品保留 LLM 打分,不阻塞 run
- 领域分组为空 → 早返回,不浪费 DB 连接

## 5. 成本追踪(§`report_pipeline/config.py` + `llm_client.py`)

- `MODELS` 每个 role 加 `price_in/price_out`(USD/M token),可通过 `LLM_PRICE_<ROLE>_IN/OUT` runtime setting 覆盖
- `call_llm_by_role` 用 token 用量 × 单价算真实 `cost_usd`,自动流入 `cluster_competition_assessments` 表

## 6. 前端成本面板(§`CompetitionCostPanel.tsx`)

Dashboard 顶栏 "Cost" 按钮 → 全屏 modal:累计汇总 + 月度趋势 + 每 run 明细(成本/token/白做率/平均信心/dominant_model)。按 `admin_ops.py` 的 `/api/v2/admin/competition-runs` 和 `/api/v2/admin/competition-costs/summary` 端点取数,旧数据(旧 run 的 cost_usd=0)按 token × 默认单价反算。

## 7. Schema 变更(`migrations/v2_040_competition_domains.sql`)

- `cluster_domain_groups` — 领域组记录(competition_run_id, domain_id, representative_cluster_id, member_seed_tools)
- `cluster_domain_members` — 领域内 cluster 映射
- `cluster_competition_assessments` 加 `domain_id` / `research_scope`(per_cluster / per_domain)
- `competition_runs` 加 `domain_count`

## 8. 测试

18 个 competition_layer 测试覆盖:领域分组纯函数(余弦相似度、去重、种子 cap)、research_domain prompt 构建、per-domain scoring、fallback per-cluster、SocialCrawl enrich 回填与失败吞掉。

## 9. 未做(显式推迟)

- 跨 run 领域 research 复用:每月只跑一次,调用量已降 76%,边际收益低
- SocialCrawl reputation 响应字段 schema 未在生产验证:防御性解析 probe 7 个常见键,建议首次生产用小 limit 验证(`--limit 5`)
