import { useEffect, useState } from 'react'
import type {
  Cluster,
  ClusterDetail,
  DimensionGroup,
  DimensionKey,
  ScoreConfig,
} from '../types'
import { useDetail } from '../hooks/useDetail'
import { audienceBadgeClasses } from '../shared/colors'
import { fmtNumber, fmtPercent } from '../shared/format'
import VelocityChart from './VelocityChart'
import MembersList from './MembersList'

export interface ClusterDetailPanelProps {
  cluster: Cluster | null
  open: boolean
  onClose: () => void
  clusterRunId: number | null
  viabilityRunId: number | null
  audienceClassifierVersion: string
  scoreConfig: ScoreConfig | null
  dateFrom?: string | null
  dateTo?: string | null
  subreddits?: string[]
}

function MetricTile({
  label,
  value,
  sub,
  onHoverChange,
  tooltip,
  emphasis,
}: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  onHoverChange?: (hover: boolean) => void
  tooltip?: React.ReactNode
  emphasis?: boolean
}) {
  const [hover, setHover] = useState(false)
  return (
    <div
      className={`relative rounded-2xl px-3 py-2.5 ring-1 transition ${
        emphasis
          ? 'bg-emerald-50/40 ring-emerald-200/60'
          : 'bg-stone-50/60 ring-stone-200/60'
      } ${onHoverChange ? 'cursor-help' : ''}`}
      onMouseEnter={() => {
        setHover(true)
        onHoverChange?.(true)
      }}
      onMouseLeave={() => {
        setHover(false)
        onHoverChange?.(false)
      }}
    >
      <div className="text-[10px] uppercase tracking-[0.08em] text-stone-500">
        {label}
      </div>
      <div className="text-lg font-medium text-stone-900 tabular-nums mt-0.5">
        {value}
      </div>
      {sub && <div className="text-[11px] text-stone-500 mt-0.5">{sub}</div>}
      {tooltip && hover && (
        <div className="absolute z-20 left-0 top-full mt-1 w-64 rounded-lg bg-stone-900 text-stone-100 text-[11px] leading-snug px-3 py-2 shadow-lg">
          {tooltip}
        </div>
      )}
    </div>
  )
}

function fmtLiftPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const pct = (value - 1) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(0)}%`
}

function BurstCard({ cluster }: { cluster: Cluster }) {
  const burst = cluster.burst
  if (!burst) return null
  const liftPct = fmtLiftPct(burst.lift)
  const corpusPct = fmtLiftPct(burst.corpus_lift)
  const badgeClass = burst.label === 'Bursting'
    ? 'bg-rose-50 text-rose-700 ring-rose-200'
    : burst.label === 'Low-volume spike'
      ? 'bg-amber-50 text-amber-700 ring-amber-200'
      : 'bg-stone-50 text-stone-500 ring-stone-200'

  return (
    <section>
      <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
        Short-term burst
      </div>
      <div className="rounded-2xl bg-white/60 ring-1 ring-stone-200/60 px-4 py-3">
        <div className="flex items-center justify-between gap-3 mb-2">
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${badgeClass}`}>
            {burst.label ?? 'No burst'}
          </span>
          <span className="text-[11px] text-stone-400">{burst.window}</span>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <div className="text-stone-500">Recent 3d</div>
          <div className="text-right tabular-nums text-stone-800">{fmtNumber(burst.recent_count)} pains</div>
          <div className="text-stone-500">Prior 28d avg</div>
          <div className="text-right tabular-nums text-stone-800">{fmtNumber(burst.baseline_count, 1)} / 3d</div>
          <div className="text-stone-500">Relative lift</div>
          <div className="text-right tabular-nums text-stone-800">{liftPct}</div>
          <div className="text-stone-500">Corpus lift</div>
          <div className="text-right tabular-nums text-stone-800">{corpusPct}</div>
          <div className="text-stone-500">z-score</div>
          <div className="text-right tabular-nums text-stone-800">{fmtNumber(burst.zscore, 1)}</div>
        </div>
      </div>
    </section>
  )
}

function fmtMarketStructure(value: string | null | undefined): string {
  if (!value) return '—'
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function CompetitionSection({ competition }: { competition: ClusterDetail['competition'] }) {
  if (!competition) return null
  const competitors = competition.competitors ?? []
  return (
    <section>
      <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
        Competition layer
      </div>
      <div className="rounded-2xl bg-white/60 ring-1 ring-stone-200/60 px-4 py-3">
        <div className="grid grid-cols-4 gap-2 mb-3">
          <MetricTile
            label="Structure"
            value={<span className="text-sm">{fmtMarketStructure(competition.market_structure)}</span>}
            sub={`run #${competition.competition_run_id}`}
          />
          <MetricTile
            label="Confidence"
            value={fmtPercent(competition.research_confidence)}
          />
          <MetricTile
            label="Ext. gap"
            value={fmtPercent(competition.external_supply_gap)}
            sub="external supply"
          />
          <MetricTile
            label="Adj. S"
            value={fmtPercent(competition.competition_adjusted_s_axis)}
            sub={`${competition.verified_direct_count ?? 0} verified direct`}
          />
        </div>
        {competition.rationale && (
          <div className="text-xs leading-relaxed text-stone-700 mb-3">
            {competition.rationale}
          </div>
        )}
        {competitors.length > 0 && (
          <div className="space-y-1.5">
            {competitors.slice(0, 5).map((item) => (
              <div
                key={`${item.category}-${item.name}-${item.url ?? ''}`}
                className="rounded-lg bg-stone-50/80 ring-1 ring-stone-200/70 px-3 py-2"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-stone-800 truncate">
                      {item.url ? (
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                          className="hover:underline"
                        >
                          {item.name}
                        </a>
                      ) : (
                        item.name
                      )}
                    </div>
                    <div className="text-[11px] text-stone-500 truncate">
                      {item.positioning || item.evidence || 'No positioning captured'}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-[10px] uppercase tracking-[0.08em] text-stone-500">
                      {item.category}
                    </div>
                    <div className="text-[11px] tabular-nums text-stone-700">
                      str {fmtPercent(item.strength_score)}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
        {competition.research_confidence !== null && competition.research_confidence < 0.5 && (
          <div className="mt-3 text-[11px] text-amber-700">
            Low-confidence research: no competition-adjusted Explore S is emitted.
          </div>
        )}
      </div>
    </section>
  )
}

const GROUP_LABEL: Record<DimensionGroup, string> = {
  quality: 'Quality',
  viability: 'Viability',
  velocity: 'Velocity',
}

function formatDimensionValue(key: DimensionKey, value: number | null): string {
  if (value !== null) return value.toFixed(2)
  if (key === 'velocity_4w_ratio_normalized') return 'low 4w volume'
  return '—'
}

export default function ClusterDetailPanel({
  cluster,
  open,
  onClose,
  clusterRunId,
  viabilityRunId,
  audienceClassifierVersion,
  scoreConfig,
  dateFrom = null,
  dateTo = null,
  subreddits = [],
}: ClusterDetailPanelProps) {
  const [ratioHover, setRatioHover] = useState(false)
  const [memberSource, setMemberSource] = useState('all')

  const { detail, velocity, corpusVelocity, members, membersPage, setMembersPage, error } =
    useDetail({
      cluster_id: open && cluster ? cluster.cluster_id : null,
      cluster_run_id: clusterRunId,
      viability_run_id: viabilityRunId,
      audience_classifier_version: audienceClassifierVersion,
      date_from: dateFrom,
      date_to: dateTo,
      subreddits,
      source: memberSource,
    })

  useEffect(() => {
    setMemberSource('all')
  }, [cluster?.cluster_id, dateFrom, dateTo, subreddits.join(',')])

  // Esc to close
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const c = cluster
  const ratioNormalized = c?.dimensions.velocity_4w_ratio_normalized?.value ?? null
  const slope = c?.dimensions.slope_per_week?.value ?? null
  const competitionS = c?.competition_adjusted_s_axis ?? null

  // Derive raw 4w / prior-4w sums from velocity weeks (between window boundaries).
  let last4wSum: number | null = null
  let prior4wSum: number | null = null
  let last4wCorpus: number | null = null
  let prior4wCorpus: number | null = null
  if (velocity) {
    const { last_4w_start, prior_4w_start, prior_4w_end } = velocity.windows
    if (last_4w_start) {
      last4wSum = velocity.weeks
        .filter((w) => w.week_start >= last_4w_start && !w.is_partial)
        .reduce((acc, w) => acc + (w.pain_count ?? 0), 0)
    }
    if (prior_4w_start && prior_4w_end) {
      prior4wSum = velocity.weeks
        .filter(
          (w) =>
            w.week_start >= prior_4w_start &&
            w.week_start < prior_4w_end &&
            !w.is_partial,
        )
        .reduce((acc, w) => acc + (w.pain_count ?? 0), 0)
    }
  }
  if (corpusVelocity && velocity) {
    const { last_4w_start, prior_4w_start, prior_4w_end } = velocity.windows
    if (last_4w_start) {
      last4wCorpus = corpusVelocity.weeks
        .filter((w) => w.week_start >= last_4w_start && !w.is_partial)
        .reduce((acc, w) => acc + (w.total_pain ?? 0), 0)
    }
    if (prior_4w_start && prior_4w_end) {
      prior4wCorpus = corpusVelocity.weeks
        .filter(
          (w) =>
            w.week_start >= prior_4w_start &&
            w.week_start < prior_4w_end &&
            !w.is_partial,
        )
        .reduce((acc, w) => acc + (w.total_pain ?? 0), 0)
    }
  }
  const rawRatio =
    last4wSum !== null && prior4wSum !== null && prior4wSum > 0
      ? last4wSum / prior4wSum
      : null

  // Dimensions grouped
  const dimensionRows = scoreConfig
    ? scoreConfig.dimensions.map((d) => {
        const cell = c?.dimensions[d.key as DimensionKey]
        return { ...d, value: cell?.value ?? null, pct: cell?.pct ?? null }
      })
    : []

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-stone-900/40 transition-opacity ${
          open ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
      />
      {/* Panel */}
      <aside
        className={`fixed top-0 right-0 z-50 h-full bg-[#FAF7F2] shadow-2xl ring-1 ring-stone-200 transition-transform duration-200 ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ width: 'min(640px, 50vw)' }}
        role="dialog"
        aria-modal="true"
      >
        {c && (
          <div className="h-full overflow-y-auto">
            {/* Sticky header */}
            <div className="sticky top-0 z-10 bg-[#FAF7F2]/95 backdrop-blur px-7 pt-5 pb-3 border-b border-stone-200/60">
              <div className="flex items-start gap-3">
                <h2 className="font-serif-display text-2xl text-stone-900 leading-tight flex-1">
                  {c.label ?? `Cluster ${c.cluster_id}`}
                </h2>
                <span
                  className={`mt-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${audienceBadgeClasses(c.audience_type)}`}
                >
                  {(c.audience_type ?? 'unclear').toLowerCase()}
                </span>
                <button
                  type="button"
                  onClick={onClose}
                  className="w-7 h-7 rounded-full ring-1 ring-stone-200 text-stone-500 hover:bg-stone-100 hover:text-stone-800 flex items-center justify-center"
                  aria-label="Close panel"
                >
                  ×
                </button>
              </div>
              <div className="text-[11px] text-stone-500 mt-1.5">
                Cluster run {clusterRunId ?? '—'} · viability run {viabilityRunId ?? '—'} ·
                classifier {audienceClassifierVersion}
              </div>
            </div>

            <div className="px-7 py-5 space-y-6">
              {error && (
                <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-1.5">
                  Failed to load detail: {error}
                </div>
              )}

              {/* Metric tiles */}
              <div className="grid grid-cols-4 gap-2">
                <MetricTile
                  label="Members"
                  value={fmtNumber(c.member_count)}
                />
                <MetricTile
                  label="Pain 4w"
                  value={fmtNumber(c.pain_count_last_4w)}
                />
                <MetricTile
                  label="Recent trend"
                  value={
                    ratioNormalized !== null ? ratioNormalized.toFixed(2) : '—'
                  }
                  sub={
                    rawRatio !== null ? (
                      <span>
                        raw {rawRatio.toFixed(2)}{' '}
                        <span className="text-stone-400">before norm.</span>
                      </span>
                    ) : ratioNormalized === null ? (
                      <span className="text-stone-400">low 4w volume</span>
                    ) : undefined
                  }
                  onHoverChange={setRatioHover}
                  emphasis={ratioHover}
                  tooltip={
                    <div>
                      <div className="font-medium mb-1">Recent trend</div>
                      <div className="mb-1 text-stone-300">
                        4w / prior-4w acceleration after corpus normalization.
                      </div>
                      <div>last 4w: {last4wSum ?? '—'}</div>
                      <div>prior 4w: {prior4wSum ?? '—'}</div>
                      <div>raw ratio: {rawRatio !== null ? rawRatio.toFixed(3) : '—'}</div>
                      <div>
                        corpus last/prior: {last4wCorpus ?? '—'} / {prior4wCorpus ?? '—'}
                      </div>
                      <div className="mt-1">
                        normalized: cluster ratio ÷ corpus ratio ={' '}
                        <span className="font-medium">
                          {ratioNormalized !== null ? ratioNormalized.toFixed(3) : '—'}
                        </span>
                      </div>
                    </div>
                  }
                />
                <MetricTile
                  label="Slope / wk"
                  value={slope !== null ? slope.toFixed(2) : '—'}
                />
              </div>

              {/* Legacy Path C ranking tiles */}
              {(c.pareto_layer !== null ||
                c.balance_score !== null ||
                c.is_eligible !== null) && (
                <div className="grid grid-cols-4 gap-2">
                  <MetricTile
                    label="Legacy Layer"
                    value={c.pareto_layer !== null ? `L${c.pareto_layer}` : '—'}
                    emphasis={c.pareto_layer === 1}
                    tooltip={
                      <div>
                        <div className="font-medium mb-1">Legacy Path C Pareto Layer</div>
                        <div>
                          Historical Path C layer using the Reddit-proxy S axis.
                        </div>
                        <div className="mt-1">
                          null = did not pass the eligibility gate (Q &amp; S ≥
                          within-audience p25), has a NULL balance score, or sits
                          beyond layer 5.
                        </div>
                      </div>
                    }
                  />
                  <MetricTile
                    label="Legacy Balance"
                    value={c.balance_score !== null ? c.balance_score.toFixed(3) : '—'}
                    tooltip={
                      <div>
                        <div className="font-medium mb-1">
                          Legacy harmonic mean of (Q, M, Reddit-proxy S)
                        </div>
                        <div>3 / (1/Q + 1/M + 1/S)</div>
                        <div className="mt-1">Q: {c.q_axis?.toFixed(3) ?? '—'}</div>
                        <div>M: {c.m_axis?.toFixed(3) ?? '—'}</div>
                        <div>legacy S: {c.s_axis?.toFixed(3) ?? '—'}</div>
                        <div className="mt-1 text-stone-300">
                          NULL when any axis falls below ε = 0.05.
                        </div>
                      </div>
                    }
                  />
                  <MetricTile
                    label="Q / M / S"
                    value={
                      c.q_axis !== null && c.m_axis !== null && competitionS !== null
                        ? `${c.q_axis.toFixed(2)} / ${c.m_axis.toFixed(2)} / ${competitionS.toFixed(2)}`
                        : '—'
                    }
                    tooltip={
                      <div>
                        <div className="font-medium mb-1">Three axes [0, 1]</div>
                        <div>Q — quality: coherence, diversity, specificity.</div>
                        <div>M — momentum: long-term slope + prior-4w volume.</div>
                        <div>
                          S — competition-adjusted external supply gap.
                        </div>
                        <div className="mt-1 text-stone-300">
                          Legacy Reddit-proxy S: {c.s_axis?.toFixed(3) ?? '—'}
                        </div>
                      </div>
                    }
                  />
                  <MetricTile
                    label="Eligible"
                    value={
                      c.is_eligible === true
                        ? '✓'
                        : c.is_eligible === false
                          ? '✗'
                          : '—'
                    }
                    sub={
                      c.low_confidence_s ? (
                        <span className="text-amber-500">low-conf S</span>
                      ) : undefined
                    }
                    tooltip={
                      <div>
                        <div className="font-medium mb-1">Eligibility (display filter)</div>
                        <div>Eligible: passed within-audience Q &amp; S ≥ p25.</div>
                        <div className="mt-1 text-stone-300">
                          Display filter only — never gates is_candidate. low-conf S
                          marks clusters with fewer than 20 tool-mentioning members,
                          where the supply-gap estimate is sparse.
                        </div>
                      </div>
                    }
                  />
                </div>
              )}

              <CompetitionSection competition={detail?.competition ?? null} />

              {/* Velocity chart */}
              <section>
                <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                  Weekly velocity
                </div>
                {velocity ? (
                  <VelocityChart
                    velocity={velocity}
                    corpus={corpusVelocity}
                    highlight={ratioHover}
                  />
                ) : (
                  <div className="text-sm text-stone-400">Loading chart…</div>
                )}
              </section>

              <BurstCard cluster={c} />

              {/* Dimensions */}
              {scoreConfig && (
                <section>
                  <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                    Dimensions
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                    {dimensionRows.map((d) => {
                      const pct = d.pct ?? 0
                      return (
                        <div
                          key={d.key}
                          className="flex items-center gap-2 text-xs"
                          title={GROUP_LABEL[d.group]}
                        >
                          <div className="w-32 truncate text-stone-600">{d.label}</div>
                          <div
                            className={`text-right tabular-nums text-stone-800 ${
                              d.key === 'velocity_4w_ratio_normalized' && d.value === null
                                ? 'w-28 text-[11px] text-stone-400 leading-tight'
                                : d.key === 'velocity_4w_ratio_normalized'
                                  ? 'w-16'
                                  : 'w-12'
                            }`}
                          >
                            {formatDimensionValue(d.key as DimensionKey, d.value)}
                          </div>
                          <div className="w-10 text-right tabular-nums text-stone-400">
                            {fmtPercent(d.pct)}
                          </div>
                          <div className="flex-1 h-1.5 bg-stone-100 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-stone-400"
                              style={{ width: `${Math.max(0, Math.min(1, pct)) * 100}%` }}
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </section>
              )}

              {/* Rationale */}
              {c.v_rationale && (
                <section>
                  <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                    Viability rationale
                  </div>
                  <div className="font-serif-display text-[15px] leading-relaxed text-stone-800 bg-white/60 rounded-2xl ring-1 ring-stone-200/60 px-4 py-3">
                    {c.v_rationale}
                  </div>
                </section>
              )}

              {/* Audience reasoning */}
              {detail?.audience?.reasoning && (
                <section>
                  <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                    Audience reasoning
                  </div>
                  <div className="text-sm leading-relaxed text-stone-700">
                    {detail.audience.reasoning}
                  </div>
                </section>
              )}

              {/* Members */}
              <section>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500">
                    Member quotes
                  </div>
                  {members?.source_counts && members.source_counts.length > 0 && (
                    <select
                      value={memberSource}
                      onChange={(e) => setMemberSource(e.target.value)}
                      className="h-7 rounded-md border border-stone-200 bg-white px-2 text-xs text-stone-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-stone-300"
                      aria-label="Filter member quotes by source"
                    >
                      <option value="all">All sources ({members.source_counts.reduce((sum, item) => sum + item.count, 0)})</option>
                      {members.source_counts.map((item) => (
                        <option key={item.source} value={item.source}>
                          {item.source} ({item.count})
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                {(dateFrom || dateTo || subreddits.length > 0 || memberSource !== 'all') && (
                  <div className="mb-2 rounded-md bg-stone-50 ring-1 ring-stone-200 px-3 py-2 text-xs text-stone-600">
                    Evidence window: {dateFrom ?? 'start'} → {dateTo ?? 'now'}
                    {subreddits.length > 0 ? ` · r/${subreddits.join(', r/')}` : ''}
                    {memberSource !== 'all' ? ` · ${memberSource}` : ''}
                  </div>
                )}
                <MembersList
                  members={members}
                  page={membersPage}
                  onPageChange={setMembersPage}
                />
              </section>
            </div>
          </div>
        )}
      </aside>
    </>
  )
}
