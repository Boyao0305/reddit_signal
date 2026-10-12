import type { AxisWeights, Cluster, ClusterCompareSummary, Weights } from '../types'
import { computeExploreScore } from '../types'
import { audienceBadgeClasses } from '../shared/colors'
import { fmtNumber, fmtPercent, truncate } from '../shared/format'
import Sparkline from './Sparkline'
import TrendChip from './TrendChip'
import ScoreCell from './ScoreCell'

export interface ClusterRowProps {
  cluster: Cluster
  weights: Weights
  axisWeights: AxisWeights
  style?: React.CSSProperties
  className?: string
  onClick?: () => void
  /** When provided, renders inline delta indicators for compare mode. */
  compareSummary?: ClusterCompareSummary | null
}

function fmtLiftPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const pct = (value - 1) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(0)}%`
}

/** Mini inline delta badge for compare mode. */
function DeltaBadge({ direction, absolute_change }: {
  direction: 'up' | 'down' | 'flat'
  absolute_change: number | null
}) {
  if (absolute_change === null) return null
  const arrow = direction === 'up' ? '↑' : direction === 'down' ? '↓' : '→'
  const color =
    direction === 'up' ? 'text-emerald-600 bg-emerald-50 ring-emerald-200' :
    direction === 'down' ? 'text-rose-500 bg-rose-50 ring-rose-200' :
    'text-stone-400 bg-stone-50 ring-stone-200'
  return (
    <span className={`ml-1 inline-flex items-center rounded-full px-1.5 py-px text-[9px] font-medium ring-1 ${color}`}>
      {arrow}{Math.abs(absolute_change) >= 10 ? Math.round(Math.abs(absolute_change)) : (absolute_change >= 0 ? '+' : '') + absolute_change.toFixed(1)}
    </span>
  )
}

export default function ClusterRow({
  cluster,
  weights,
  axisWeights,
  style,
  className,
  onClick,
  compareSummary,
}: ClusterRowProps) {
  const c = cluster
  const ratio = c.dimensions.velocity_4w_ratio_normalized?.value ?? null
  const slope = c.dimensions.slope_per_week?.value ?? null
  const exploreScore = computeExploreScore(c, axisWeights)
  const displayLayer = c.competition_adjusted_pareto_layer
  const displayBalance = c.competition_adjusted_balance_score
  const usesCompetition = c.s_axis_source === 'competition_adjusted'
  const burst = c.burst ?? null
  const burstLabel = burst?.label ?? null
  const burstLiftPct = fmtLiftPct(burst?.lift)
  const corpusLiftPct = fmtLiftPct(burst?.corpus_lift)
  const burstClass = burstLabel === 'Bursting'
    ? 'bg-rose-50 text-rose-700 ring-rose-200'
    : burstLabel === 'Low-volume spike'
      ? 'bg-amber-50 text-amber-700 ring-amber-200'
      : burstLabel === 'Emerging'
        ? 'bg-sky-50 text-sky-700 ring-sky-200'
        : 'bg-stone-50 text-stone-400 ring-stone-200'
  const burstTitle = burst
    ? [
        `Recent 3d: ${fmtNumber(burst.recent_count)} pains`,
        `Prior 28d avg: ${fmtNumber(burst.baseline_count, 1)} / 3d`,
        `Relative lift: ${burstLiftPct}`,
        `Corpus lift: ${corpusLiftPct}`,
        `z-score: ${fmtNumber(burst.zscore, 1)}`,
      ].join('\n')
    : ''

  // Pareto layer colour: L1 emerald-600 bold, L2 stone-600, deeper layers stone-400.
  const layerColor =
    displayLayer === 1
      ? 'text-emerald-600 font-semibold'
      : displayLayer === 2
        ? 'text-stone-600'
        : 'text-stone-400'

  return (
    <div
      style={style}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onClick()
              }
            }
          : undefined
      }
      // Design-language card: keep rounded-[20px], the layered stone shadow, and the
      // stone hover ring. Do NOT regress to rounded-lg / shadow-sm / violet hover ring.
      className={`${className ?? 'grid'} h-full bg-white rounded-[20px] ring-1 ring-stone-200/60 shadow-[0_1px_3px_rgba(28,25,23,0.05),0_4px_14px_rgba(28,25,23,0.04)] px-5 py-2 hover:ring-stone-300/80 hover:shadow-[0_2px_6px_rgba(28,25,23,0.07),0_8px_24px_rgba(28,25,23,0.06)] transition-all ${onClick ? 'cursor-pointer' : ''}`}
    >
      {/* Cluster label */}
      <div className="truncate pr-2 text-sm text-stone-900" title={c.label ?? ''}>
        {c.label ?? `Cluster ${c.cluster_id}`}
      </div>

      {/* Audience — keep the rounded pill badge */}
      <div>
        <span
          className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-medium ${audienceBadgeClasses(
            c.audience_type,
          )}`}
        >
          {(c.audience_type ?? 'unclear').toLowerCase()}
        </span>
      </div>

      {/* Members */}
      <div className="text-right text-xs tabular-nums text-stone-700">
        {fmtNumber(c.member_count)}
        {compareSummary?.matched && compareSummary.member_count_delta && (
          <DeltaBadge
            direction={compareSummary.member_count_delta.direction}
            absolute_change={compareSummary.member_count_delta.absolute_change}
          />
        )}
      </div>

      {/* Pain 4w */}
      <div className="text-right text-xs tabular-nums text-stone-700">
        {fmtNumber(c.pain_count_last_4w)}
      </div>

      {/* Trend — sparkline + slope chip */}
      <div className="flex items-center gap-1.5">
        <Sparkline
          data={c.sparkline}
          partialFromIndex={c.sparkline_partial_from_index}
        />
        <TrendChip value={slope} kind="slope" />
      </div>

      {/* 4w ratio */}
      <div className="text-right">
        <TrendChip value={ratio} kind="ratio" />
      </div>

      {/* Burst */}
      <div className="text-right">
        {burstLabel ? (
          <span
            title={burstTitle}
            className={`inline-block rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ${burstClass}`}
          >
            {burstLabel === 'Bursting' ? 'Burst' : burstLabel === 'Low-volume spike' ? 'Low vol' : 'New'}
          </span>
        ) : (
          <span title={burstTitle} className="text-xs text-stone-300">—</span>
        )}
      </div>

      {/* Pareto layer */}
      <div
        className={`text-right text-xs tabular-nums ${layerColor}`}
        title={usesCompetition ? 'Competition-adjusted Pareto layer' : 'No competition-adjusted layer yet'}
      >
        {displayLayer !== null && displayLayer !== undefined ? `L${displayLayer}` : '—'}
        {compareSummary?.matched && compareSummary.pareto_layer_delta && (
          <DeltaBadge
            direction={compareSummary.pareto_layer_delta.direction}
            absolute_change={compareSummary.pareto_layer_delta.absolute_change}
          />
        )}
      </div>

      {/* Balance score */}
      <div
        className="text-right text-xs tabular-nums text-stone-600"
        title={usesCompetition ? 'Competition-adjusted balance score' : 'No competition-adjusted balance score yet'}
      >
        {fmtPercent(displayBalance)}
        {compareSummary?.matched && compareSummary.balance_score_delta && (
          <DeltaBadge
            direction={compareSummary.balance_score_delta.direction}
            absolute_change={compareSummary.balance_score_delta.absolute_change}
          />
        )}
      </div>

      {/* Explore ✦ — reflects the Q/M/S axis sliders; violet text only here */}
      <div
        className="text-right text-xs tabular-nums font-medium text-violet-600"
        title={usesCompetition ? 'Explore uses competition-adjusted S axis' : 'No competition-adjusted S axis yet'}
      >
        {exploreScore !== null ? fmtPercent(exploreScore) : '—'}
      </div>

      {/* Legacy 14-dim score */}
      <div className="pr-2 text-right text-xs">
        <ScoreCell cluster={c} weights={weights} />
      </div>

      {/* Rationale */}
      <div
        className="truncate border-l border-stone-200/70 pl-4 text-xs text-stone-600"
        title={c.v_rationale ?? ''}
      >
        {truncate(c.v_rationale, 80)}
      </div>
    </div>
  )
}
