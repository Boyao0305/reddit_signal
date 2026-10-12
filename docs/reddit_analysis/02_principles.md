
> How decisions get made on this project.
> The locked-decisions file records *what* was decided; this file records *how* we decide.

## Architecture

**Layer separation by regen cost.** Raw is sacred (refetch is expensive). Projected is rebuildable from raw via local SQL. Derived is rebuildable from projected, sometimes with LLM cost. Storage and lifecycle policy follow this gradient.

**Additive schema migrations only.** No destructive change in the same step as the introduction of its replacement. New column / new table → backfill → validate → only then drop the old one. The window between addition and deletion is where mistakes get caught.

**Versioned runs everywhere derived data is recomputable.** `cluster_run_id`, `viability_run_id`, `prompt_version`. New work goes into a new run; old runs stay queryable. Re-running is always an additive operation, never a mutation.

**Plug-in by folder, not by flag.** A new source, a new metric, a new scorer is a new file at a known path. Configuration files that reroute behavior are last resort.

**No premature composites.** Each dimension stays a separate column until there is empirical evidence that combining them produces a metric the user actually filters on. Composites are easy to add later; hard to remove without breaking everything that depends on them.

## Calibration

**Show distributions before picking a threshold.** No threshold is approved without seeing the histogram or quartiles of the metric on real data. Round-numbered defaults (0.5, 0.6) are flags for "this was guessed."

**Recalibrate per run, not once.** Every monthly re-cluster gets its own threshold pass. The 90d window has different shape from the full corpus; consecutive 90d windows can drift 10-25%. Reusing last month's threshold without checking is a silent failure mode.

**Cut at p10 by default.** Tier-1 deterministic gates default to "drop the bottom 10%" via p10 of the distribution. More aggressive cuts need an empirical reason; less aggressive cuts let in known-bad clusters.

**Calibration is reproducible, not eyeballed.** Manual labeling of pairs is acceptable as a last resort but not as the default. Distribution-based methods that re-run automatically each month are preferred.

## Execution

**Backup before destructive operations.** `pg_dump` before any DROP COLUMN, schema rewrite, or batch UPDATE. The cost of the backup is always lower than the cost of a wrong revert.

**Validate before cutover.** Parallel-run new code against old, diff outputs, require ≤1% drift before flipping the feature flag. The validation step is the gate, not the deployment.

**Idempotent migrations always.** `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, UPSERT on UNIQUE keys. Migrations should be runnable twice in a row without effect.

**Background long-running jobs.** Foreground tool timeouts kill long psql operations mid-transaction. Anything over 60 seconds runs in the background with completion notification.

**Fix ownership on any psql-created object.** New tables created via `sudo -u postgres psql` are owned by postgres, not the application role. Always include `ALTER TABLE ... OWNER TO llmanalyse` after migration.

**Ingestion errors go to a DLQ, not retries-forever.** `pain_extraction_attempts` records `failed=true` after N retries; the row stops blocking and gets revisited explicitly.

## Scope

**Build cheaper layers before expensive ones.** Prune the funnel deterministically before paying for LLM judgment. Quality gate before viability gate before competition crawl. The expensive step always operates on a small, pre-validated set.

**Don't build infrastructure ahead of validating need.** Cluster lineage was deferred until 3+ monthly runs were planned. Dashboard v2 is deferred until competition columns exist. Building too early ossifies the wrong abstraction.

**Defer until the question is concrete.** Open architectural questions stay in `04_open_questions.md` until they have a forcing function. Speculation does not earn implementation budget.

## Communication

**Continuity over restart for in-progress work.** The conversation thread carries decision context. /new only at clean phase boundaries with a written handoff doc.

**Compress at 70% context, force at 85%.** Long sessions naturally degrade; the cost of mid-session compression beats the cost of a confused agent.

**Stop at planned checkpoints.** Re-cluster done → verify before audience. Audience done → verify before viability. Don't auto-chain phases without an explicit "continue."

**Justify tool/approach choices, decide decisively.** Ambiguity is fine to surface; indecision is not. After the trade-offs are laid out, pick.

**Regular progress reports during long jobs.** Background runs of more than ~10 minutes get a status update. The agent does not "go dark" on the user.

## Hard "no"s

- **No composite scores in user-facing outputs.** This has been re-litigated; the answer is no until proven otherwise empirically.
- **No deletion of L1 raw data, ever.** Refetch is expensive. Disk is cheap.
- **No threshold guessing without distribution evidence.**
- **No mid-conversation introduction of unrelated work** (the agent does not propose multi-source ingestion when the user asked about temporal aggregation).
- **No claims of completion without verification.** "Done" requires a check query or test, not an assumption.

## Skepticism patterns to maintain

- **Numerical projections in unfamiliar regimes are usually wrong.** Disk estimates, time estimates, cost estimates from first principles tend to be off by an order of magnitude. Validate small first, scale only after measuring.
- **Polished-looking proposals are not necessarily correct.** A markdown table with five clean columns can encode a bad architectural choice as easily as a good one. Push on the choice itself, not the format.
- **LLM-generated SQL/code that "looks right" often references columns or behaviors that don't exist.** Always run the query before committing to the design that depends on it.