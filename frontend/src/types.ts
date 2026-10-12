// Contract types matching dashboard_v2 API responses (spec §5.2 / api.py).

export type DimensionKey =
  | 'coherence'
  | 'thread_diversity'
  | 'author_diversity'
  | 'subreddit_count'
  | 'specificity_mean'
  | 'workaround_ratio'
  | 'separation'
  | 'temporal_spread_days'
  | 'q_specificity'
  | 'v_buildability'
  | 'v_icp_clarity'
  | 'v_moat_potential'
  | 'slope_per_week'
  | 'velocity_4w_ratio_normalized'

export type DimensionGroup = 'quality' | 'viability' | 'velocity'

export interface DimensionCell {
  value: number | null
  pct: number | null
}

export type DimensionsMap = Record<DimensionKey, DimensionCell>

export interface Cluster {
  cluster_id: number
  label: string | null
  audience_type: string | null
  audience_confidence: number | null
  icp_hint: string | null
  member_count: number | null
  pain_count_last_4w: number | null
  v_rationale: string | null
  q_noise_flag: boolean | null
  // Path C ranking fields (v1.5.1)
  pareto_layer: number | null
  balance_score: number | null
  q_axis: number | null
  m_axis: number | null
  s_axis: number | null
  is_eligible: boolean | null
  low_confidence_s: boolean | null
  competition_run_id?: number | null
  competition_status?: string | null
  market_structure?: string | null
  competition_confidence?: number | null
  external_supply_gap?: number | null
  competition_adjusted_s_axis?: number | null
  competition_adjusted_balance_score?: number | null
  competition_adjusted_pareto_layer?: number | null
  s_axis_source?: 'competition_adjusted' | 'missing' | string | null
  burst?: {
    score: number | null
    lift: number | null
    zscore: number | null
    recent_count: number | null
    baseline_count: number | null
    corpus_lift: number | null
    label: 'Bursting' | 'Low-volume spike' | 'Emerging' | string | null
    window: '3d_vs_prior_28d' | string
  } | null
  filter_window?: {
    pain_count: number | null
    days_active: number | null
    subreddit_count: number | null
    date_from: string | null
    date_to: string | null
    subreddits: string[]
  }
  dimensions: DimensionsMap
  sparkline: Array<number | null>
  sparkline_weeks: string[]
  sparkline_partial_from_index: number
}

export interface ClustersResponse {
  cluster_run_id: number
  viability_run_id: number
  audience_classifier_version: string
  total: number
  total_is_exact?: boolean
  page: number
  page_size: number
  has_more: boolean
  clusters: Cluster[]
  corpus_burst?: CorpusBurst | null
  freshness?: Freshness | null
  emerging_opportunities?: EmergingOpportunity[]
}

export interface Freshness {
  cluster_run_id: number
  map_window_start: string | null
  map_window_end: string | null
  map_live_at: string | null
  snapshot_at: string | null
  corpus_data_through_day: string | null
  cluster_data_through_day: string | null
  signal_data_through_day: string | null
  last_daily_job_at: string | null
}

export interface EmergingOpportunity {
  id: number
  emerging_run_id: number
  cluster_run_id: number
  label: string
  pain_count: number
  author_count: number
  subreddit_count: number
  coherence: number
  burst_score: number
  source_window_start: string | null
  source_window_end: string | null
  sample_pain_ids: number[]
  sample_quotes: Array<{
    pain_id: number
    pain_statement: string | null
    raw_quote: string | null
    subreddit_name: string | null
    source_author: string | null
    source_created: string | null
  }>
  created_at: string | null
  lookback_days: number | null
}

/** Run-level corpus burst sentinel — unclustered (orphan) pain surge vs corpus. */
export interface CorpusBurst {
  cluster_run_id: number
  day_anchor: string | null
  total_recent: number | null
  total_baseline: number | null
  orphan_recent: number | null
  orphan_baseline: number | null
  orphan_lift: number | null
  orphan_zscore: number | null
  orphan_label: 'Bursting' | 'Low-volume spike' | string | null
  corpus_lift: number | null
  snapshot_at: string | null
}

export interface ClusterRun {
  id: number
  window_start: string | null
  window_end: string | null
  live_at: string | null
  cluster_count: number | null
  is_legacy: boolean
  status: 'live' | 'staging' | 'archived' | string
  signal_data_through_day?: string | null
  snapshot_at?: string | null
}

export interface ViabilityRun {
  id: number
  cluster_run_id: number
  n_clusters: number | null
  llm_model: string | null
}

export interface RunsResponse {
  cluster_runs: ClusterRun[]
  viability_runs: ViabilityRun[]
  defaults: {
    cluster_run_id: number | null
    viability_run_id: number | null
    audience_classifier_version: string
  }
}

export interface ScoreDimensionConfig {
  key: DimensionKey
  label: string
  group: DimensionGroup
  default_weight: number
  missing_policy: 'skip' | string
}

export interface ScoreConfig {
  groups: Array<{ key: DimensionGroup; label: string }>
  dimensions: ScoreDimensionConfig[]
}

export type Weights = Record<DimensionKey, number>

// Explore axis weights — used by the exploration score panel (right sidebar).
// All three default to 1/3 (equal weighting).
export interface AxisWeights {
  q: number  // Quality axis weight  [0, 1]
  m: number  // Momentum axis weight [0, 1]
  s: number  // Supply gap axis weight [0, 1]
}

export const DEFAULT_AXIS_WEIGHTS: AxisWeights = { q: 1, m: 1, s: 1 }

/** Compute the exploration score for a cluster given axis weights.
 *  Simple weighted average of Q, M, and the competition-derived S axis.
 *  Returns null when all three axis values are null (unranked cluster).
 */
export function computeExploreScore(
  cluster: Pick<Cluster, 'q_axis' | 'm_axis' | 's_axis' | 'competition_adjusted_s_axis'>,
  w: AxisWeights,
): number | null {
  const total = w.q + w.m + w.s
  if (total === 0) return null
  const q = cluster.q_axis
  const m = cluster.m_axis
  const s = cluster.competition_adjusted_s_axis
  if ((w.q > 0 && q == null) || (w.m > 0 && m == null) || (w.s > 0 && s == null)) {
    return null
  }
  const num = (w.q * (q ?? 0)) + (w.m * (m ?? 0)) + (w.s * (s ?? 0))
  return num / total
}

export interface SurfaceDistributionEntry {
  count: number
  audience_prior: string | null
}

export interface ClusterDetail {
  cluster_id: number
  audience: {
    reasoning: string | null
    sample_pain_ids: number[]
    surface_distribution: Record<string, SurfaceDistributionEntry>
  } | null
  viability: {
    sample_pain_ids: number[]
    llm_input_tokens: number | null
    llm_output_tokens: number | null
  } | null
  competition: {
    competition_run_id: number
    competition_status: string
    market_structure: string
    research_confidence: number | null
    external_supply_gap: number | null
    competition_intensity: number | null
    competition_adjusted_s_axis: number | null
    solution_gap_evidence: number | null
    verified_direct_count: number | null
    direct_competitor_count: number | null
    rationale: string | null
    updated_at: string | null
    competitors: Array<{
      name: string
      url: string | null
      category: 'direct' | 'adjacent' | 'incumbent' | string
      pricing: string | null
      business_model: string | null
      positioning: string | null
      evidence: string | null
      confidence: 'verified' | 'unverified' | string
      relevance_score: number | null
      strength_score: number | null
      satisfaction_gap: number | null
    }>
  } | null
}

export interface VelocityWeek {
  week_start: string
  pain_count: number | null
  share_global: number | null
  share_field: number | null
  is_partial: boolean
}

export interface VelocityResponse {
  cluster_id: number
  cluster_run_id: number
  median_pain_count: number | null
  weeks: VelocityWeek[]
  windows: {
    last_4w_start: string | null
    prior_4w_start: string | null
    prior_4w_end: string | null
  }
}

export interface CorpusVelocityWeek {
  week_start: string
  total_pain: number | null
  is_partial: boolean
}

export interface CorpusVelocityResponse {
  cluster_run_id: number
  weeks: CorpusVelocityWeek[]
}

export interface Member {
  pain_id: number
  pain_statement: string | null
  raw_quote: string | null
  source: string | null
  source_surface: string | null
  subreddit_name: string | null
  source_author: string | null
  source_created: string | null
  specificity: number | null
  sentiment_score: number | null
  is_workaround: boolean | null
  mentioned_tools: string[]
}

export interface MembersResponse {
  cluster_id: number
  total: number
  page: number
  page_size: number
  pages: number
  filter_window?: {
    date_from: string | null
    date_to: string | null
    subreddits: string[]
    source?: string | null
  }
  source_counts?: Array<{
    source: string
    count: number
  }>
  members: Member[]
}

export interface ClusterFilterOptionsResponse {
  cluster_run_id: number
  date_min: string | null
  date_max: string | null
  subreddits: Array<{
    subreddit_name: string
    pain_count: number
  }>
}

// ─── Report pipeline (Phase 3C) ──────────────────────────────────────────────

export type ReportVerdict = 'go' | 'no_go' | 'conditional'

export interface ReportJob {
  job_id: number
  cluster_id?: number
  cluster_run_id?: number | null
  viability_run_id?: number | null
  audience_classifier_version?: string | null
  status: 'queued' | 'running' | 'done' | 'failed'
  current_stage: number
  stage_label?: string
  cost_usd: number
  error_log: string | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface ReportJobListResponse {
  cluster_id: number
  jobs: ReportJob[]
}

export interface ReportJobStageOutput {
  stage_name: string
  stage_order: number
  model_used: string | null
  raw_output: unknown
  tokens_in: number | null
  tokens_out: number | null
  cost_usd: number
  created_at: string | null
}

export interface ReportJobDetail {
  job: ReportJob
  stages: ReportJobStageOutput[]
  report: {
    report_id: number
    report_md: string
    created_at: string | null
  } | null
}

export interface Report {
  report_id: number
  job_id: number
  cluster_id: number
  verdict: ReportVerdict | null
  verdict_reason: string | null
  product_logic: string | null
  why_no_direct: string | null
  opportunity_or_trap: string | null
  condition_difficulty: string | null
  condition_summary: string | null
  competitors: unknown
  market_structure: string | null
  report_md: string
  created_at: string | null
}

export interface SystemSetting {
  key: string
  label: string
  group: string
  value: string
  is_secret: boolean
  description: string
  source: string
}

export interface SystemSettingsResponse {
  settings: SystemSetting[]
}

export interface AdapterCapabilities {
  produces_pain: boolean
  produces_competitor: boolean
  produces_traction: boolean
  produces_temporal: boolean
}

export interface AdapterMeta {
  name: string
  display_name: string
  description: string
  source_file: string
  capabilities: AdapterCapabilities
  prompt_registered: boolean
}

export interface SourceRuntimeConfig {
  source: string
  registered: boolean
  enabled: boolean
  fetch_enabled: boolean
  project_enabled: boolean
  extract_enabled: boolean
  query: string
  lookback_days: number
  fetch_limit: number
  priority: number
  notes: string
  updated_at: string | null
  adapter_meta?: AdapterMeta
}

export interface SourcesResponse {
  sources: SourceRuntimeConfig[]
}

export interface AdaptersResponse {
  adapters: AdapterMeta[]
}

export interface SourceUpdatePayload {
  enabled?: boolean
  fetch_enabled?: boolean
  project_enabled?: boolean
  extract_enabled?: boolean
  query?: string
  lookback_days?: number
  fetch_limit?: number
  priority?: number
  notes?: string
}

export interface DailyPlanPreviewStep {
  order: number
  phase: 'fetch' | 'project' | 'extract'
  source: string
  query?: string
  lookback_days?: number
  fetch_limit?: number
}

export interface DailyPlanPreviewResponse {
  steps: DailyPlanPreviewStep[]
}

export interface SourceSimulationItem {
  kind?: string | null
  id?: string | number | null
  surface?: string | null
  author?: string | null
  title?: string | null
  text?: string | null
  url?: string | null
  event_time?: string | null
  engagement_score?: number | null
  metrics?: Record<string, unknown>
}

export interface SourceSimulationResponse {
  source: string
  dry_run: boolean
  writes: number
  count: number
  request: {
    provider?: string
    endpoint?: string
    params?: Record<string, unknown>
    venue?: Record<string, unknown>
    managed?: boolean
    candidate_subreddits?: string[]
  }
  config: {
    query: string
    lookback_days: number
    limit: number
    since: string | null
    until: string
  }
  items: SourceSimulationItem[]
  raw_sample?: Record<string, unknown>
}

export type RedditSubredditStatus = 'candidate' | 'active' | 'paused' | 'rejected'
export type RedditSubredditSort =
  | 'priority'
  | 'pain_yield'
  | 'b2c_contribution'
  | 'assigned_rate'
  | 'pain_objects'
  | 'last_evaluated'

export interface RedditSubredditSource {
  subreddit_name: string
  status: RedditSubredditStatus
  priority: number
  daily_lookback_days: number
  backfill_months: number
  backfill_enabled: boolean
  tags: string[]
  notes: string
  last_fetch_at: string | null
  last_backfill_at: string | null
  last_evaluated_at: string | null
  metrics: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

export interface RedditSubredditsResponse {
  subreddits: RedditSubredditSource[]
}

export interface RedditBackfillResponse {
  dry_run: boolean
  chunk_count: number
  job?: {
    id: number
    status: string
    total_chunks: number
    created_at: string | null
  }
}

export type SocialVenueStatus = 'candidate' | 'active' | 'paused'
export type SocialVenueSort = 'priority' | 'last_fetch' | 'name'

export interface SocialSourceVenue {
  id: number
  source: 'facebook' | 'twitter'
  venue_type: string
  name: string
  url_or_id: string
  status: SocialVenueStatus
  priority: number
  daily_lookback_days: number
  fetch_limit: number
  sort_mode: string
  notes: string
  metrics: Record<string, unknown>
  last_fetch_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface SocialVenuesResponse {
  venues: SocialSourceVenue[]
}

export type SourceBackfillMode = 'fetch' | 'project' | 'fetch-project' | 'extract'
export type SourceBackfillScope = 'active' | 'candidate' | 'all' | 'selected' | 'query'

export interface SourceBackfillChunk {
  id?: number
  job_id?: number
  source: string
  target_type: string
  target_id: string
  target_label: string
  query: string
  window_start: string
  window_end: string
  coverage_mode: 'full_window' | 'best_effort_window' | 'recent_only' | 'unsupported'
  coverage_note: string
  mode: SourceBackfillMode
  status?: string
  rows_fetched?: number
  rows_projected?: number
  rows_extracted?: number
  details?: Record<string, unknown> | null
  created_at?: string | null
  updated_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  error?: string | null
}

export interface SourceBackfillJob {
  id: number
  status: string
  sources: string[]
  scope: SourceBackfillScope
  mode: SourceBackfillMode
  date_from: string
  date_to: string
  chunk_days: number
  requested_by: string | null
  total_chunks: number
  queued_chunks: number
  running_chunks: number
  succeeded_chunks: number
  failed_chunks: number
  skipped_chunks: number
  empty_chunks: number
  cancelled_chunks?: number
  rows_fetched: number
  rows_projected: number
  rows_extracted: number
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  cancel_requested_at?: string | null
  cancel_requested_by?: string | null
}

export interface SourceBackfillQueueWorker {
  worker_id: string
  mode: string
  started_at: string | null
  last_seen_at: string | null
  current_job_id: number | null
  version: string | null
  seconds_since_seen?: number | null
}

export interface SourceBackfillQueuePipelineJob {
  id: number
  job_type: PipelineJobType
  status: PipelineJobStatus
  requested_by: string | null
  request_source: string
  run_after: string | null
  started_at: string | null
  finished_at: string | null
  worker_id: string | null
  exit_code: number | null
  error: string | null
  log_tail: string | null
  params: Record<string, unknown>
  cancel_requested_at?: string | null
  cancel_requested_by?: string | null
  created_at: string
  updated_at: string
}

export interface SourceBackfillQueueJobState {
  job_id: number
  status: string
  queue_position: number | null
  reason: string
}

export interface SourceBackfillQueueStatus {
  state: 'idle' | 'waiting' | 'running' | 'blocked' | 'paused' | string
  message: string
  worker_enabled: boolean
  worker_online: boolean
  worker_online_seconds: number
  workers: SourceBackfillQueueWorker[]
  blocking_pipeline_job: SourceBackfillQueuePipelineJob | null
  active_pipeline_jobs: SourceBackfillQueuePipelineJob[]
  running_chunks: SourceBackfillChunk[]
  queued_chunks: number
  running_chunks_count: number
  jobs: SourceBackfillQueueJobState[]
}

export interface SourceBackfillPlan {
  dry_run: boolean
  sources: string[]
  scope: SourceBackfillScope
  mode: SourceBackfillMode
  date_from: string
  date_to: string
  chunk_days: number
  target_count: number
  chunk_count: number
  source_counts: Record<string, number>
  coverage_counts: Record<string, number>
  chunks: SourceBackfillChunk[]
  job?: SourceBackfillJob
}

export interface SourceBackfillDetail {
  job: SourceBackfillJob
  chunks: SourceBackfillChunk[]
  running_chunks?: SourceBackfillChunk[]
  queue?: SourceBackfillQueueStatus
  chunk_pagination?: {
    total: number
    limit: number
    offset: number
    page: number
    page_size: number
    pages: number
  }
}

export interface SchedulerStatus {
  scheduler_running: boolean
  worker_running?: boolean
  worker_error?: string | null
  workers?: Array<{
    worker_id: string
    mode: string
    started_at: string | null
    last_seen_at: string | null
    current_job_id: number | null
    version: string | null
  }>
  recent_jobs?: Array<{
    id: number
    job_type: PipelineJobType
    status: PipelineJobStatus
    requested_by: string | null
    request_source: string
    run_after: string | null
    started_at: string | null
    finished_at: string | null
    worker_id: string | null
    exit_code: number | null
    error: string | null
    log_tail: string | null
    params: Record<string, unknown>
    created_at: string
    updated_at: string
  }>
  daily_utc: string
  monthly_utc: string
  daily_beijing?: string
  monthly_beijing?: string
  next_daily_utc?: string
  next_monthly_utc?: string
  next_daily_beijing?: string
  next_monthly_beijing?: string
  snapshot?: {
    enabled: boolean
    refresh_minutes: string
    state: {
      cluster_run_id: number
      viability_run_id: number
      audience_classifier_version: string
      status: string
      started_at: string | null
      finished_at: string | null
      duration_seconds: number | null
      row_count: number | null
      error: string | null
      last_success_at: string | null
      last_success_row_count: number | null
      updated_at: string | null
    } | null
  }
}

export interface SchedulerTriggerResponse {
  triggered: PipelineJobType
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  job?: NonNullable<SchedulerStatus['recent_jobs']>[number]
}

export interface ClusterReportResponse {
  cluster_id: number
  report: Report | null
  latest_job: ReportJob | null
}

// ─── Pipeline job history (admin troubleshooting) ────────────────────────────

export type PipelineJobType = 'daily' | 'monthly' | 'extraction'
export type PipelineJobStatus =
  | 'queued'
  | 'running'
  | 'cancel_requested'
  | 'cancelled'
  | 'succeeded'
  | 'failed'
export type PipelineStepStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled'

export interface PipelineJob {
  id: number
  job_type: PipelineJobType
  status: PipelineJobStatus
  requested_by: string | null
  request_source: string
  run_after: string | null
  started_at: string | null
  finished_at: string | null
  worker_id: string | null
  exit_code: number | null
  error: string | null
  log_tail: string | null
  params: Record<string, unknown>
  cancel_requested_at: string | null
  cancel_requested_by: string | null
  created_at: string
  updated_at: string
}

export interface PipelineJobStep {
  id: number
  job_id: number
  stage_name: string
  stage_order: number
  status: PipelineStepStatus
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  error: string | null
  log_tail: string | null
  created_at: string
  updated_at: string
}

export interface PipelineJobEvent {
  id: number
  job_id: number
  stage_name: string | null
  event_type: string
  message: string
  details: Record<string, unknown>
  created_at: string
}

export interface PipelineJobsResponse {
  jobs: PipelineJob[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface PipelineJobDetail {
  job: PipelineJob
  steps: PipelineJobStep[]
  events: PipelineJobEvent[]
}

export interface PipelineJobStats {
  total: number
  succeeded: number
  failed: number
  running: number
  success_rate: number
  avg_duration_seconds: number | null
  failures_by_stage: Array<{ stage_name: string; count: number }>
}

// ─── Competition layer cost tracking ──────────────────────────────────────────

export interface CompetitionRun {
  id: number
  status: 'queued' | 'running' | 'succeeded' | 'failed' | string
  limit_count: number
  audience_type: string
  scope: string
  is_active: boolean
  requested_by: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  assessed_count: number
  total_tokens_in: number
  total_tokens_out: number
  estimated_cost_usd: number
  cost_source: 'stored' | 'estimated' | string
  null_s_axis_count: number
  wasted_ratio: number | null
  avg_confidence: number | null
  dominant_model: string | null
}

export interface CompetitionRunsResponse {
  runs: CompetitionRun[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface CompetitionCostBucket {
  assessed_count: number
  total_tokens_in: number
  total_tokens_out: number
  estimated_cost_usd: number
  cost_source: 'stored' | 'estimated' | string
  null_s_axis_count: number
  wasted_ratio: number | null
}

export interface CompetitionCostMonth extends CompetitionCostBucket {
  month: string
  run_count: number
}

export interface CompetitionCostCumulative extends CompetitionCostBucket {
  total_runs: number
  total_assessed: number
  total_null_s_axis: number
}

export interface CompetitionCostSummary {
  monthly: CompetitionCostMonth[]
  cumulative: CompetitionCostCumulative
}

// ── Cluster cross-run comparison ─────────────────────────────────────────────

export interface MetricDelta {
  ref_value: number | null
  cmp_value: number | null
  absolute_change: number | null
  percent_change: number | null
  direction: 'up' | 'down' | 'flat'
}

export interface ClusterRunSnapshot {
  cluster_id: number
  member_count: number | null
  pain_count_last_4w: number | null
  q_axis: number | null
  m_axis: number | null
  s_axis: number | null
  competition_adjusted_s_axis: number | null
  pareto_layer: number | null
  balance_score: number | null
  competition_adjusted_pareto_layer: number | null
  competition_adjusted_balance_score: number | null
  market_structure: string | null
  external_supply_gap: number | null
  is_eligible: boolean | null
  burst: Cluster['burst']
  dimensions: DimensionsMap
  sparkline: Array<number | null>
  sparkline_weeks: string[]
  velocity: VelocityWeek[]
  competition: ClusterDetail['competition']
}

export interface ClusterCompareResponse {
  cluster_id: number
  ref_run_id: number
  cmp_run_id: number
  ref_viability_run_id?: number
  cmp_viability_run_id?: number
  lineage: {
    ref_cluster_id: number
    cmp_cluster_id: number
    relation: 'continued' | 'split' | 'merged' | 'born' | 'died' | 'unmatched'
    overlap_jaccard: number | null
    shared_members: number | null
  }
  ref: ClusterRunSnapshot | null
  cmp: ClusterRunSnapshot | null
  deltas: {
    member_count: MetricDelta
    pain_count_last_4w: MetricDelta
    pareto_layer: MetricDelta
    balance_score: MetricDelta
    competition_adjusted_pareto_layer: MetricDelta
    competition_adjusted_balance_score: MetricDelta
    competition_adjusted_s_axis: MetricDelta
    external_supply_gap: MetricDelta
    market_structure_changed: boolean
    market_structure_ref: string | null
    market_structure_cmp: string | null
    new_competitors: string[]
    dropped_competitors: string[]
  }
}

/** Lightweight per-row compare summary used for inline delta indicators. */
export interface ClusterCompareSummary {
  cluster_id: number
  matched: boolean
  ref_cluster_id?: number | null
  cmp_cluster_id?: number | null
  relation?: ClusterCompareResponse['lineage']['relation']
  overlap_jaccard?: number | null
  member_count_delta: MetricDelta
  pareto_layer_delta: MetricDelta
  balance_score_delta: MetricDelta
  s_axis_delta: MetricDelta
}
