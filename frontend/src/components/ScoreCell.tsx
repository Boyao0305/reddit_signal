import { useState } from 'react'
import { createPortal } from 'react-dom'
import type { Cluster, DimensionKey, Weights } from '../types'
import { computeScore, scoreBreakdown } from '../lib/score'
import { useScoreConfig } from '../hooks/useScoreConfig'

export { computeScore } from '../lib/score'

// Re-exported for App.tsx backwards-compat (used as fallback weights before
// score-config arrives).
export const DEFAULT_WEIGHTS: Weights = {
  coherence: 1.0,
  thread_diversity: 1.0,
  author_diversity: 1.0,
  subreddit_count: 1.0,
  specificity_mean: 1.0,
  workaround_ratio: 1.0,
  separation: 1.0,
  temporal_spread_days: 1.0,
  q_specificity: 1.0,
  v_buildability: 1.0,
  v_icp_clarity: 1.0,
  v_moat_potential: 1.0,
  slope_per_week: 0.0,
  velocity_4w_ratio_normalized: 0.0,
}

export interface ScoreCellProps {
  cluster: Cluster
  weights: Weights
}

export default function ScoreCell({ cluster, weights }: ScoreCellProps) {
  const [hover, setHover] = useState(false)
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null)
  const { data: config } = useScoreConfig()
  const labels: Partial<Record<DimensionKey, string>> = {}
  if (config) for (const d of config.dimensions) labels[d.key] = d.label

  const s = computeScore(cluster, weights)
  const rows = scoreBreakdown(cluster, weights, labels)
  const included = rows.filter((r) => r.included)
  const totalW = included.reduce((a, r) => a + r.weight, 0)

  return (
    <span
      className="relative inline-block"
      onMouseEnter={(event) => {
        setAnchorRect(event.currentTarget.getBoundingClientRect())
        setHover(true)
      }}
      onMouseLeave={() => setHover(false)}
    >
      {s === null ? (
        <span className="text-stone-400 tabular-nums">—</span>
      ) : (
        <span className="tabular-nums font-medium text-stone-900">
          {(s * 100).toFixed(0)}%
        </span>
      )}
      {hover && anchorRect && createPortal(
        <ScoreTooltip
          anchorRect={anchorRect}
          score={s}
          rows={rows}
          totalWeight={totalW}
        />,
        document.body,
      )}
    </span>
  )
}

function ScoreTooltip({
  anchorRect,
  score,
  rows,
  totalWeight,
}: {
  anchorRect: DOMRect
  score: number | null
  rows: ReturnType<typeof scoreBreakdown>
  totalWeight: number
}) {
  const width = 340
  const margin = 12
  const left = Math.min(
    Math.max(margin, anchorRect.right - width),
    window.innerWidth - width - margin,
  )
  const top = Math.min(anchorRect.bottom + 8, window.innerHeight - 420)

  return (
    <div
      className="fixed z-[9999] w-[340px] rounded-lg bg-[#FBF8F3] p-3 text-xs text-stone-700 ring-1 ring-stone-200 shadow-[0_12px_32px_rgba(28,25,23,0.18)]"
      style={{ left, top: Math.max(margin, top), pointerEvents: 'none' }}
    >
      <div className="mb-2 flex items-baseline justify-between font-semibold text-stone-900">
        <span>Score breakdown</span>
        <span className="tabular-nums">
          {score === null ? '—' : `${(score * 100).toFixed(1)}%`}
        </span>
      </div>
      <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-2 gap-y-0.5">
        <div className="text-[10px] uppercase tracking-wide text-stone-500">Dim</div>
        <div className="text-right text-[10px] uppercase tracking-wide text-stone-500">w</div>
        <div className="text-right text-[10px] uppercase tracking-wide text-stone-500">pct</div>
        <div className="text-right text-[10px] uppercase tracking-wide text-stone-500">contrib</div>
        {[...rows]
          .sort((a, b) => {
            // included first (sorted by contribution desc), then skipped
            if (a.included && !b.included) return -1
            if (!a.included && b.included) return 1
            return b.contribution - a.contribution
          })
          .map((r) => (
            <RowLine key={r.key} row={r} />
          ))}
        <div className="col-span-4 mt-1 flex justify-between border-t border-stone-200/70 pt-1">
          <span className="text-stone-500">total weight</span>
          <span className="tabular-nums">{totalWeight.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}

function RowLine({ row }: { row: ReturnType<typeof scoreBreakdown>[number] }) {
  const muted = !row.included
  return (
    <>
      <div className={`truncate ${muted ? 'text-stone-400' : ''}`}>{row.label}</div>
      <div className={`text-right tabular-nums ${muted ? 'text-stone-400' : ''}`}>
        {row.weight.toFixed(1)}
      </div>
      <div className={`text-right tabular-nums ${muted ? 'text-stone-400' : ''}`}>
        {row.pct === null ? '—' : (row.pct * 100).toFixed(0) + '%'}
      </div>
      <div className={`text-right tabular-nums ${muted ? 'text-stone-400' : ''}`}>
        {row.included ? (row.contribution * 100).toFixed(1) : '—'}
      </div>
    </>
  )
}
