# Nonlinear composite — deferred design

> **Status:** Deferred. Captured for future reference, not in active scope.
> **Created:** 2026-05-12
> **Trigger to reopen:** see §4 below.
> **Companion:** `backtest_harness.md` (same folder); `02_components/dashboard_v2_design.md` (planned).

## 1. Why this was considered

The dashboard v2 composite (`exploratory_score`) ranks clusters by a user-tunable weighted sum of within-run percentile-transformed dimensions: coherence, thread_diversity, author_diversity, specificity_mean, workaround_ratio, separation, the LLM 1–5 verdicts, and optionally a velocity contribution. Linear with adjustable weights, by design — see `01_locked_decisions.md` for the rationale on parameterized vs. fixed composites.

A pure linear sum makes three assumptions that almost certainly don't hold for SaaS opportunity quality:

- **Independence between dimensions.** Each dimension contributes the same regardless of the others. False — high coherence on a 30-member cluster is noisy signal; high coherence on a 500-member cluster is strong signal. The dimensions interact.
- **Constant marginal returns.** Coherence 0.5 → 0.7 worth the same as 0.7 → 0.9. False — empirically there is likely a threshold (around 0.6, based on observed run_id=9 clusters) where the cluster "clicks." Pure linear scoring spends slider budget linearly across a regime where the actual return is sigmoidal.
- **No conjunctive structure beyond hard gates.** False — `growing ∧ low-competition ∧ specific` matters in a way an additive sum cannot express, because near-zero on any of those should pull the score down sharply, not just by a slider's weight.

These are real weaknesses, not academic ones. Linear scoring still produces a ranking the founder can sort and filter, but it will be systematically wrong in ways that take backtest data to detect — see `backtest_harness.md` for that companion plan.

## 2. The three approaches considered, ordered by complexity

### 2.1 Pre-transform individual dimensions before summing (low complexity)

Apply a function to each dimension, then linear-combine the transforms. The composite stays a weighted sum, but the response is now nonlinear.

Examples of useful transforms:
- `log(member_count)` for diminishing returns on volume
- `sigmoid((coherence − 0.6) × 8)` for a soft threshold at the empirical inflection point
- `min(velocity_ratio, 1.5)` clipped to prevent outlier ratios dominating
- `sqrt(workaround_ratio)` for fast-then-flattening response

These encode domain priors cheaply. Each slider's contribution acquires shape without breaking the interpretability of the UI — the user still sees one slider per dimension, but each slider's effect on the score is now nonlinear in the underlying metric. The hover-breakdown ("this cluster scored X on this dimension") shows the post-transform value alongside the raw, so the user can see what the transform did.

Cost: 20–50 lines added to the score-computation logic in the API. Each transform documented in the UI hover.

### 2.2 Engineered interaction features (medium complexity)

Compute conjunctive features in SQL or the API and expose them as new dimensions in the same UI. Candidates:

- `is_dense_and_growing = (last_4w > p75) × (norm_ratio > 1)` — recent activity AND positive trajectory
- `well_constructed = min(coherence, thread_diversity, author_diversity)` — a bottleneck metric; cluster is only as good as its weakest dimension
- `under_radar = (member_count > p75) × (subreddit_count > p50)` — high volume despite topic concentration
- `coherent_and_specific = coherence × specificity_mean` — multiplicative AND for two correlated quality dimensions

These are domain-encoded nonlinearities surfaced as ordinary dimensions in the linear UI. The user sees them and tunes them with sliders like anything else. The trick is that the dimension itself is already nonlinear in the underlying signals.

Cost: each feature is one SQL column or API computation. Adding them to the UI is mechanical once the framework exists. Useful set is probably 3–6 features.

### 2.3 Multiplicative for the must-have core (higher complexity)

Replace the linear sum with a hybrid: geometric mean for must-have features, arithmetic mean for the additive features.

```
score = geomean(must_haves) × sum(weight_i × dim_i)
```

Encodes "all of these need to be at least decent" without zeroing out everything for one weak dim. Useful for the gate-adjacent dimensions where the founder wants "no single failure mode" rather than "high average."

Cost: harder to interpret. The slider UI no longer cleanly maps to additive contribution. Probably not worth it without empirical evidence that the additive failure modes are real and costly.

## 3. Recommended sequence if reopened

1. Pre-transforms first — low cost, high interpretability gain, no UI changes.
2. Engineered interaction features second — adds dimensions but they're inspectable.
3. Multiplicative core deferred indefinitely — only revisit if observed slider behavior shows the founder zeroing out a specific dimension and getting nonsense rankings.

## 4. What triggers reopening

Concrete triggers, ordered by likelihood:

- **Slider behavior reveals problems.** If, after 2–3 weeks of dashboard use, the founder consistently sets one dimension's slider to ~0 to suppress its influence, that's evidence the linear contribution is wrong for that dimension. Specifically, the slider is being used to cancel out an artifact of linearity (e.g., very-high coherence on small clusters dragging them above better candidates). Pre-transforms (§2.1) would solve this directly.
- **Backtest results.** If the backtest harness shows GBT or random forest models meaningfully beating linear on held-out SaaS-survival prediction, that's empirical evidence the linear assumption is costing signal. Pre-transforms + engineered features (§2.1 + §2.2) are the natural lift from there.
- **A new dimension that's fundamentally non-additive lands.** Competition density when L5 ships, if it interacts with viability (e.g., "low competition only matters for highly viable clusters"), needs an interaction term, not an additive slider.

## 5. What this does NOT do

- This is not a learned model. The transforms and interaction features are still hand-designed and inspectable. They encode priors, not data.
- This does not replace the user-tunable slider UI. Sliders still set weights; the only thing that changes is what each slider weights against.
- This does not affect any hard gate. `is_real_pain ∧ is_software_solvable` and the audience/member_count gates remain unchanged.

## 6. Cost estimate if reopened

- Pre-transforms: 1–2 days. Mostly: pick transforms, run them against run_id=10 distributions to verify they don't pathologize, write tests, document in UI hover text.
- Engineered features: 2–4 days for 3–6 features. Same workflow per feature.
- Total to ship §2.1 + §2.2: ~1 week of focused work, no new infrastructure.
