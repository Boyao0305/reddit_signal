import { useEffect, useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import type {
  AxisWeights,
  Cluster,
  ClusterCompareSummary,
  CorpusBurst,
  EmergingOpportunity,
  Weights,
} from '../types'
import { computeExploreScore } from '../types'
import ClusterRow from './ClusterRow'
import { computeScore } from './ScoreCell'

export interface ClusterTableProps {
  clusters: Cluster[]
  weights: Weights
  axisWeights: AxisWeights
  onSelect?: (cluster_id: number) => void
  onLoadMore?: () => void
  hasMore?: boolean
  loadingMore?: boolean
  initialSortKey?: SortKey
  initialSortDir?: SortDir
  corpusBurst?: CorpusBurst | null
  emergingOpportunities?: EmergingOpportunity[]
  /** Map of cluster_id -> compare summary for inline delta indicators. */
  compareSummaries?: Record<number, ClusterCompareSummary> | null
}

type SortKey =
  | 'label'
  | 'audience_type'
  | 'member_count'
  | 'pain_count_last_4w'
  | 'slope_per_week'
  | 'velocity_4w_ratio_normalized'
  | 'burst_score'
  | 'pareto_layer'
  | 'balance_score'
  | 'explore_score'
  | 'score'
  | 'v_rationale'

type SortDir = 'asc' | 'desc'

const COLUMNS: Array<{ key: SortKey; label: string; align?: 'left' | 'right'; title?: string }> = [
  { key: 'label', label: 'Cluster' },
  { key: 'audience_type', label: 'Audience' },
  { key: 'member_count', label: 'Members', align: 'right' },
  { key: 'pain_count_last_4w', label: 'Pain 4w', align: 'right' },
  { key: 'slope_per_week', label: 'Trend' },
  { key: 'velocity_4w_ratio_normalized', label: '4w ratio', align: 'right' },
  { key: 'burst_score', label: 'Burst', align: 'right', title: '3d vs prior 28d short-term spike' },
  { key: 'pareto_layer', label: 'Layer', align: 'right', title: 'Pareto layer (1 = best)' },
  { key: 'balance_score', label: 'Balance', align: 'right', title: 'Harmonic mean of Q·M·S' },
  { key: 'explore_score', label: 'Explore ✦', align: 'right', title: 'Weighted Q/M/S — drag right-panel sliders to re-rank' },
  { key: 'score', label: 'Score', align: 'right', title: 'Legacy 14-dim weighted score' },
  { key: 'v_rationale', label: 'Rationale' },
]

export const GRID_TEMPLATE_CLASS =
  'grid grid-cols-[minmax(180px,2fr)_72px_72px_72px_88px_60px_68px_52px_72px_76px_76px_minmax(220px,3fr)] items-center'

function compareNullable<T>(a: T | null | undefined, b: T | null | undefined): number {
  const aNull = a === null || a === undefined
  const bNull = b === null || b === undefined
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b))
}

function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(0)}%`
}

function EmergingOpportunities({ items }: { items: EmergingOpportunity[] }) {
  if (!items.length) return null
  return (
    <section className="mx-2 mb-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2">
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-emerald-800">
          Emerging opportunities
        </div>
        <div className="text-[11px] text-emerald-700">
          orphan pain micro-clusters
        </div>
      </div>
      <div className="grid gap-1.5">
        {items.slice(0, 5).map((item) => {
          const sample = item.sample_quotes?.[0]
          return (
            <div
              key={item.id}
              className="grid grid-cols-[minmax(180px,1fr)_72px_72px_72px_minmax(180px,1.2fr)] items-center gap-3 rounded-md bg-white/70 px-2.5 py-1.5 text-xs ring-1 ring-emerald-100"
            >
              <div className="min-w-0">
                <div className="truncate font-medium text-stone-900" title={item.label}>
                  {item.label}
                </div>
                <div className="text-[11px] text-stone-500">
                  {item.source_window_start ?? '—'} → {item.source_window_end ?? '—'}
                </div>
              </div>
              <div className="text-right tabular-nums text-stone-700">
                {item.pain_count} pains
              </div>
              <div className="text-right tabular-nums text-stone-700">
                {item.subreddit_count} subs
              </div>
              <div className="text-right tabular-nums text-stone-700">
                {fmtPct(item.coherence)}
              </div>
              <div
                className="truncate text-[11px] text-stone-600"
                title={sample?.pain_statement ?? ''}
              >
                {sample?.pain_statement ?? 'No sample captured'}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

export default function ClusterTable({
  clusters,
  weights,
  axisWeights,
  onSelect,
  onLoadMore,
  hasMore = false,
  loadingMore = false,
  initialSortKey = 'pareto_layer',
  initialSortDir = 'asc',
  corpusBurst = null,
  emergingOpportunities = [],
  compareSummaries = null,
}: ClusterTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>(initialSortKey)
  const [sortDir, setSortDir] = useState<SortDir>(initialSortDir)

  useEffect(() => {
    setSortKey(initialSortKey)
    setSortDir(initialSortDir)
  }, [initialSortKey, initialSortDir])

  const sorted = useMemo(() => {
    const arr = clusters.slice()
    arr.sort((a, b) => {
      let cmp = 0
      switch (sortKey) {
        case 'label':
          cmp = compareNullable(a.label, b.label)
          break
        case 'audience_type':
          cmp = compareNullable(a.audience_type, b.audience_type)
          break
        case 'member_count':
          cmp = compareNullable(a.member_count, b.member_count)
          break
        case 'pain_count_last_4w':
          cmp = compareNullable(a.pain_count_last_4w, b.pain_count_last_4w)
          break
        case 'slope_per_week':
          cmp = compareNullable(
            a.dimensions.slope_per_week?.value,
            b.dimensions.slope_per_week?.value,
          )
          break
        case 'velocity_4w_ratio_normalized':
          cmp = compareNullable(
            a.dimensions.velocity_4w_ratio_normalized?.value,
            b.dimensions.velocity_4w_ratio_normalized?.value,
          )
          break
        case 'burst_score':
          cmp = compareNullable(a.burst?.score, b.burst?.score)
          break
        case 'pareto_layer':
          cmp = compareNullable(
            a.competition_adjusted_pareto_layer,
            b.competition_adjusted_pareto_layer,
          )
          break
        case 'balance_score':
          cmp = compareNullable(
            a.competition_adjusted_balance_score,
            b.competition_adjusted_balance_score,
          )
          break
        case 'explore_score':
          cmp = compareNullable(
            computeExploreScore(a, axisWeights),
            computeExploreScore(b, axisWeights),
          )
          break
        case 'score':
          cmp = compareNullable(computeScore(a, weights), computeScore(b, weights))
          break
        case 'v_rationale':
          cmp = compareNullable(a.v_rationale, b.v_rationale)
          break
      }
      return sortDir === 'asc' ? cmp : -cmp
    })
    return arr
  }, [clusters, sortKey, sortDir, weights, axisWeights])

  const parentRef = useRef<HTMLDivElement | null>(null)
  const lastAutoLoadSizeRef = useRef<number | null>(null)
  const virt = useVirtualizer({
    count: sorted.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 76,
    overscan: 8,
  })

  const virtualItems = virt.getVirtualItems()

  useEffect(() => {
    lastAutoLoadSizeRef.current = null
  }, [clusters])

  useEffect(() => {
    if (!hasMore || loadingMore || !onLoadMore || virtualItems.length === 0) return
    const last = virtualItems[virtualItems.length - 1]
    if (
      last.index >= Math.max(0, sorted.length - 8) &&
      lastAutoLoadSizeRef.current !== sorted.length
    ) {
      lastAutoLoadSizeRef.current = sorted.length
      onLoadMore()
    }
  }, [hasMore, loadingMore, onLoadMore, sorted.length, virtualItems])

  function onHeaderClick(key: SortKey) {
    if (key === sortKey) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      if (key === 'pareto_layer') {
        setSortDir('asc')
      } else {
        const numericKeys: SortKey[] = [
          'member_count', 'pain_count_last_4w', 'slope_per_week',
          'velocity_4w_ratio_normalized', 'burst_score', 'balance_score', 'explore_score', 'score',
        ]
        setSortDir(numericKeys.includes(key) ? 'desc' : 'asc')
      }
    }
  }

  return (
    <div className="flex flex-col h-full">
      {corpusBurst && corpusBurst.orphan_label ? (
        <div className="mx-2 mb-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs text-sky-800">
          检测到未聚类痛点激增（近 3d 孤儿 {corpusBurst.orphan_recent ?? 0} 条，
          lift {((corpusBurst.orphan_lift ?? 1) - 1) * 100 >= 0 ? '+' : ''}
          {(((corpusBurst.orphan_lift ?? 1) - 1) * 100).toFixed(0)}%），可能存在月中新兴话题，待下次月度聚类纳入。
        </div>
      ) : null}
      <EmergingOpportunities items={emergingOpportunities} />
      <div
        className={`${GRID_TEMPLATE_CLASS} px-9 pb-2 text-[10px] font-semibold text-stone-500 uppercase tracking-[0.08em]`}
      >
        {COLUMNS.map((col, i) => {
          const isActive = sortKey === col.key
          const arrow = !isActive ? '' : sortDir === 'asc' ? ' ▲' : ' ▼'
          const isExplore = col.key === 'explore_score'
          return (
            <button
              key={`${col.key}-${i}`}
              onClick={() => onHeaderClick(col.key)}
              title={col.title}
              className={`text-${col.align ?? 'left'} hover:text-stone-900 ${
                isActive ? 'text-stone-900' : ''
              } ${isExplore ? 'text-violet-500 hover:text-violet-700' : ''} ${
                col.key === 'v_rationale' ? 'border-l border-stone-200/70 pl-4' : ''
              } cursor-pointer`}
              type="button"
            >
              {col.label}
              {arrow}
            </button>
          )
        })}
      </div>
      <div ref={parentRef} className="flex-1 overflow-auto">
        <div
          style={{
            height: `${virt.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {virtualItems.map((vi) => {
            const c = sorted[vi.index]
            return (
              <div
                key={c.cluster_id}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: `${vi.size}px`,
                  transform: `translateY(${vi.start}px)`,
                  padding: '7px 16px',
                }}
              >
                <ClusterRow
                  cluster={c}
                  weights={weights}
                  axisWeights={axisWeights}
                  className={GRID_TEMPLATE_CLASS}
                  onClick={onSelect ? () => onSelect(c.cluster_id) : undefined}
                  compareSummary={compareSummaries?.[c.cluster_id] ?? null}
                />
              </div>
            )
          })}
        </div>
      </div>
      <div className="h-10 flex items-center justify-center border-t border-stone-200/70 text-xs text-stone-500">
        {loadingMore ? (
          'Loading more…'
        ) : hasMore ? (
          <button
            type="button"
            onClick={onLoadMore}
            className="px-3 py-1 rounded-md ring-1 ring-stone-300 text-stone-700 hover:bg-stone-100"
          >
            Load more
          </button>
        ) : sorted.length > 0 ? (
          'All loaded'
        ) : (
          'No clusters'
        )}
      </div>
    </div>
  )
}
