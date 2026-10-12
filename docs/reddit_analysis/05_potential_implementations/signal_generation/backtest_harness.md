# Backtest harness — deferred design

> **Status:** Deferred. Captured for future reference, not in active scope.
> **Created:** 2026-05-12
> **Earliest realistic start:** after dashboard v2 has shipped and accumulated 2–3 months of slider-use data. See §6 below.
> **Companion:** `nonlinear_composite.md` (same folder — the model side); `02_components/dashboard_v2_design.md` (the consumer of evidence).

## 1. Why this matters

The dashboard v2's `exploratory_score` defaults its slider weights to "what the founder guesses matters." A backtest harness replaces that default with "what historically predicted SaaS survival." The slider UI stays — the founder can still override data-driven weights with their own priors — but the *defaults* become grounded in evidence rather than intuition. That's the difference between a tool that reflects your intuition and a tool that calibrates it.

This also feeds the `nonlinear_composite.md` decision: if learned models beat linear-with-transforms on held-out predictions, we have evidence to bring nonlinearity into the system. If they don't, we have evidence to stay linear and stop spending engineering effort on model complexity.

## 2. Architecture — six-step pipeline

### Step 1: Historical anchor dates

Pick 2–3 anchor dates: e.g., 2018-01-01, 2020-01-01, 2022-01-01. At each anchor T0, the pipeline runs *only* on data that existed before T0. No data leakage from the future; no concept-drift contamination from later content.

Practical implication: pain extraction, clustering, audience classification, viability scoring all rerun with `window_end = T0`. The output is "what a dashboard user on date T0 would have seen" for that anchor.

### Step 2: Frozen pipeline runs at each anchor

End-to-end pipeline rerun per anchor: L1 raw → L2 signal_events → L3 pain_objects → L4 clusters/audience/quality/viability → L4 temporal aggregates. Each anchor produces one full snapshot. Stored under fresh `analysis_runs.id` values with `params.backtest_anchor = T0`.

The HDBSCAN clusters at T0 are NOT the same shapes as the clusters at 2026-05. That's the point — we're asking "given clusters as they would have appeared then, which ones spawned surviving SaaS?"

### Step 3: Match historical SaaS products to clusters

For each cluster at T0, find SaaS products that launched in [T0, T0 + 24 months] and target the cluster's pain.

Sources of SaaS launch data:
- Crunchbase — launch dates, descriptions, funding rounds
- AlternativeTo — problem framing per product
- ProductHunt archives — launch announcements with launch date
- App Store / Play Store — launch metadata
- GitHub — open-source proxies (useful for B2B / developer-tooling clusters)

Matching pipeline:
1. Embed each SaaS's description with the same 384-dim model used for pain_objects.
2. Cosine-similarity vs. cluster centroid at T0.
3. Threshold at e.g. 0.5 (to be calibrated).
4. LLM-verify candidate matches with a structured prompt: "does this product target the pain described by this cluster?"
5. Build a small gold set of ~100 hand-labelled pairs to calibrate the threshold and verify LLM accuracy.

### Step 4: Pull outcomes

For each matched SaaS, look up status as of the present:
- Still operating? (yes/no)
- Reached revenue threshold? (SimilarWeb traffic proxy or Crunchbase funding-round proxy)
- Acquired? (treat as success)
- Raised Series A+? (treat as proxy for "real business")

Aggregate to a per-cluster binary label: "this cluster spawned ≥1 surviving company that crossed threshold X." Optionally also a continuous label: "this cluster spawned N surviving companies." Both are useful — binary for classification, continuous for regression and ordering quality.

### Step 5: Supervised training

Inputs: cluster features at T0 — every dimension the dashboard exposes plus the cluster centroid.
Labels: outcomes through T0 + 5 years.

Candidate models:
- **Linear regression with the founder's current dashboard weights** — baseline 1, replicates the dashboard's composite exactly. Answers: "how well does my intuition predict?"
- **Linear regression with learned weights** — baseline 2, asks: "what would *evidence* weight these at?" The deltas vs. baseline 1 are diagnostic.
- **Gradient-boosted trees (XGBoost / LightGBM)** — captures nonlinearity, still inspectable via SHAP feature importance.
- **A small neural net or random forest** — richer nonlinearity, less inspectable. Compare to confirm GBT isn't underfitting.

Evaluate each on held-out anchor dates. Train on 2018+2020, test on 2022. Rotate. Metrics: precision@k (top-50 predictions), NDCG@k (full ranking quality), AUC for the binary task.

### Step 6: Leaderboard report

A small markdown report compares ranking quality against three baselines:
- "Rank by member_count alone"
- "Rank by velocity alone"
- "Rank by the founder's current slider weights"

A model only earns production status if it beats all three on held-out data. If it doesn't — and this is genuinely possible — the linear-with-transforms approach is correctly preserving epistemic humility, and we've learned that more model complexity isn't justified.

## 3. Smallest viable pilot

Before committing to the full multi-anchor pipeline, run a single-anchor pilot to derisk the question "do our features have *any* predictive power?"

Scope:
- One anchor date (suggest 2022-01-01 — recent enough for accurate outcome data, far enough back for a 4-year outcome window).
- Rerun the pipeline through L4 with `window_end = T0`.
- Hand-match 50–100 SaaS products to clusters (no LLM step yet — manual is fine at this scale, gives calibration data for the eventual automated matcher).
- Train one linear regression + one GBT on the resulting (features, label) pairs.
- Compare AUC vs. three baselines.

Cost: ~3–4 weeks of focused work, ~$200–500 in API costs for the pipeline rerun (mostly pain extraction over historical Reddit at Gemini Flash Lite rates).

If even the pilot shows features correlated with SaaS survival above chance, the full backtest is justified. If not, it's an important null result and the cluster-feature design itself needs revisiting before more model complexity is justified.

## 4. Honest limits

These are real and need to appear in any report drawn from this work:

- **Survival bias is unfixable.** We only see SaaS that *launched*. Cluster-shaped pains that should have spawned a product but didn't (because no founder noticed) are invisible to the label. The implicit population is "pains a founder did try to address," not "pains worth addressing."
- **The SaaS-to-cluster matching is the bottleneck.** Embedding + LLM verification gets you most of the way but edge cases will be plentiful (rebrands, multi-pain products, pivots after launch). The hand-labelled gold set is genuinely necessary for both threshold calibration and confidence in the resulting numbers.
- **Drift across years.** Reddit 2018 ≠ Reddit 2026. The set of active subreddits, posting patterns, audience composition have shifted. Features that predict 2018 outcomes may not transfer to 2026. The fix is multi-anchor training (model learns drift-invariant signal) plus a temporal hold-out (test specifically on a later year).
- **Outcome ambiguity.** "Surviving" is fuzzy. A product can technically still operate but be effectively dead. A product can be acquired and folded into another. The label needs to be operationalized concretely (e.g., "still listed on App Store with ≥1 review in the last 6 months" or "Crunchbase Series A+ funded") and the choice affects results.
- **L5 competition data not yet available.** Until the L5 layer is designed and populated, competition is a missing dimension for backtesting. Models trained without it may attribute its variance to other dimensions and produce misleading feature importances. Phase ordering: dashboard ships → competition lands → backtest.

## 5. Cost summary

- Pilot: 3–4 weeks, $200–500.
- Full multi-anchor: 2–4 months total, $500–1500 in API costs across 2–3 anchors. Most cost is historical Reddit pain extraction.
- Maintenance: a re-run of the most recent anchor every 6–12 months keeps the calibration current. ~3 days of work + ~$200.

## 6. Decision triggers

Reopen this design when ALL of these are true:
- Dashboard v2 has been in active use for ≥2 months.
- The founder has expressed an opinion (positive or negative) about the default slider weights.
- L5 competition data is either populated or has a definite ETA.
- There is no higher-priority Component competing for the same resources.

Earliest realistic start: around July 2026, assuming dashboard ships in early June.

## 7. Open questions (deferred until reopening)

- Which SaaS data sources to use as primary? Crunchbase has good launch metadata but covers a slice; ProductHunt has wider coverage but less rigor on dates.
- What threshold defines "survival"? Active listing, revenue proxy, funding round, acquisition?
- How to handle multi-cluster matches? A product targeting two pain points — does it count toward both clusters' labels equally?
- Should the harness include B2B clusters even though the dashboard's current funnel is B2C-first?
