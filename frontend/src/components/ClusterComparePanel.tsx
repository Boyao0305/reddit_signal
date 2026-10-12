import { useEffect, useRef, useState } from 'react'
import type {
  Cluster,
  ClusterCompareResponse,
  DimensionKey,
  MetricDelta,
} from '../types'
import { fetchClusterCompare } from '../api/clusters'
import { fmtNumber, fmtPercent } from '../shared/format'
import VelocityChart from './VelocityChart'

export interface ClusterComparePanelProps {
  cluster: Cluster | null
  open: boolean
  onClose: () => void
  refRunId: number | null
  cmpRunId: number | null
  viabilityRunId: number | null
  refViabilityRunId?: number | null
  cmpViabilityRunId?: number | null
  audienceClassifierVersion: string
}

type Tab = 'collection' | 'analysis' | 'market'

function DeltaCell({ delta }: { delta: MetricDelta }) {
  if (delta.ref_value === null && delta.cmp_value === null) {
    return <span className="text-stone-300 text-xs">—</span>
  }
  const d = delta.direction
  const color =
    d === 'up' ? 'text-emerald-600' : d === 'down' ? 'text-rose-500' : 'text-stone-400'
  const arrow = d === 'up' ? '↑' : d === 'down' ? '↓' : '→'
  const absStr =
    delta.absolute_change !== null
      ? `${delta.absolute_change >= 0 ? '+' : ''}${fmtNumber(delta.absolute_change, 1)}`
      : ''
  const pctStr =
    delta.percent_change !== null
      ? ` (${delta.percent_change >= 0 ? '+' : ''}${(delta.percent_change * 100).toFixed(1)}%)`
      : ''
  return (
    <span className={`text-xs font-medium tabular-nums ${color}`}>
      {arrow} {absStr}{pctStr}
    </span>
  )
}

function MetricRow({
  label,
  refValue,
  cmpValue,
  delta,
  fmt = (v: number | null) => (v !== null ? fmtNumber(v) : '—'),
  compact = false,
}: {
  label: string
  refValue: number | null
  cmpValue: number | null
  delta?: MetricDelta
  fmt?: (v: number | null) => string
  compact?: boolean
}) {
  return (
    <div className={`grid grid-cols-[1fr_2fr_1fr_2fr] gap-2 items-center ${compact ? 'text-xs' : 'text-sm'}`}>
      <div className="text-stone-500 text-[11px] uppercase tracking-[0.05em]">{label}</div>
      <div className={`tabular-nums text-stone-800 ${compact ? '' : 'font-medium'}`}>
        {fmt(refValue)}
      </div>
      <div className="text-center">
        {delta ? <DeltaCell delta={delta} /> : <span className="text-stone-300">—</span>}
      </div>
      <div className={`tabular-nums text-stone-800 ${compact ? '' : 'font-medium'}`}>
        {fmt(cmpValue)}
      </div>
    </div>
  )
}

function fmtLayer(v: number | null): string {
  if (v === null || v === undefined) return '—'
  return `L${v}`
}

function fmtMarket(v: string | null): string {
  if (!v) return '—'
  return v
    .split('_')
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' ')
}

export default function ClusterComparePanel({
  cluster,
  open,
  onClose,
  refRunId,
  cmpRunId,
  viabilityRunId,
  refViabilityRunId,
  cmpViabilityRunId,
  audienceClassifierVersion,
}: ClusterComparePanelProps) {
  const [data, setData] = useState<ClusterCompareResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('collection')
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open || !cluster || !refRunId || !cmpRunId || !viabilityRunId) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchClusterCompare({
      cluster_id: cluster.cluster_id,
      ref_run_id: refRunId,
      cmp_run_id: cmpRunId,
      viability_run_id: viabilityRunId,
      ref_viability_run_id: refViabilityRunId,
      cmp_viability_run_id: cmpViabilityRunId,
      audience_classifier_version: audienceClassifierVersion,
    })
      .then((resp) => {
        if (!cancelled) {
          setData(resp)
          setLoading(false)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e))
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [
    open,
    cluster?.cluster_id,
    refRunId,
    cmpRunId,
    viabilityRunId,
    refViabilityRunId,
    cmpViabilityRunId,
    audienceClassifierVersion,
  ])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    if (open) {
      document.addEventListener('keydown', onKey)
      document.body.style.overflow = 'hidden'
    }
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [open, onClose])

  if (!open || !cluster) return null

  const lineage = data?.lineage
  const ref = data?.ref
  const cmp = data?.cmp
  const deltas = data?.deltas

  const tabs: { key: Tab; label: string }[] = [
    { key: 'collection', label: '采集对比' },
    { key: 'analysis', label: '分析对比' },
    { key: 'market', label: '市场对比' },
  ]

  return (
    <>
      {/* backdrop */}
      <div
        className="fixed inset-0 z-40 bg-stone-900/20 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* panel */}
      <div
        ref={panelRef}
        className="fixed right-0 top-0 z-50 h-full w-[min(720px,55vw)] bg-[#FAF7F2] shadow-2xl ring-1 ring-stone-200/60 overflow-y-auto"
        style={{ scrollbarWidth: 'thin' }}
      >
        {/* header */}
        <div className="sticky top-0 z-10 bg-[#FAF7F2]/90 backdrop-blur-sm border-b border-stone-200/60 px-6 py-4">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <button
                onClick={onClose}
                className="text-[11px] text-stone-400 hover:text-stone-600 transition mb-1"
              >
                ← Back
              </button>
              <h2 className="text-base font-semibold text-stone-900 truncate">
                Cluster Compare: {cluster.label ?? `#${cluster.cluster_id}`}
              </h2>
            </div>
            {/* lineage badge */}
            {lineage && (
              <div className="shrink-0 flex items-center gap-1.5 text-[11px]">
                <span
                  className={`rounded-full px-2 py-0.5 font-medium ring-1 ${
                    lineage.relation === 'continued'
                      ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
                      : lineage.relation === 'unmatched'
                        ? 'bg-stone-50 text-stone-500 ring-stone-200'
                        : 'bg-amber-50 text-amber-700 ring-amber-200'
                  }`}
                >
                  {lineage.relation}
                </span>
                {lineage.overlap_jaccard !== null && (
                  <span className="text-stone-400">
                    Jaccard {fmtPercent(lineage.overlap_jaccard)}
                  </span>
                )}
                {lineage.shared_members !== null && (
                  <span className="text-stone-400">
                    {lineage.shared_members} shared
                  </span>
                )}
              </div>
            )}
          </div>

          {/* Run labels */}
          <div className="flex items-center gap-2 mt-2 text-[11px] text-stone-500">
            <span>Ref: Run #{refRunId}</span>
            <span className="text-stone-300">vs</span>
            <span>Cmp: Run #{cmpRunId}</span>
          </div>
        </div>

        {/* loading / error */}
        <div className="px-6 py-4">
          {loading && (
            <div className="text-sm text-stone-400 animate-pulse">Loading comparison…</div>
          )}
          {error && (
            <div className="text-sm text-rose-600 bg-rose-50 rounded-lg px-4 py-3 ring-1 ring-rose-200">
              {error}
            </div>
          )}
        </div>

        {!loading && !error && data && (
          <>
            {/* tabs */}
            <div className="flex gap-0.5 px-6 border-b border-stone-200/60">
              {tabs.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`px-3 py-2 text-sm font-medium transition border-b-2 -mb-[1px] ${
                    tab === t.key
                      ? 'text-stone-900 border-stone-800'
                      : 'text-stone-400 border-transparent hover:text-stone-600'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* column headers */}
            <div className="grid grid-cols-[1fr_2fr_1fr_2fr] gap-2 px-6 py-2 text-[10px] uppercase tracking-[0.06em] text-stone-400 border-b border-stone-100">
              <div />
              <div>Ref (Run #{refRunId})</div>
              <div className="text-center">Δ</div>
              <div>Cmp (Run #{cmpRunId})</div>
            </div>

            <div className="px-6 py-4 space-y-4">
              {/* ── Collection Tab ── */}
              {tab === 'collection' && (
                <>
                  <section className="space-y-2">
                    <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-1">
                      Volume &amp; Reach
                    </div>
                    <MetricRow
                      label="Members"
                      refValue={ref?.member_count ?? null}
                      cmpValue={cmp?.member_count ?? null}
                      delta={deltas?.member_count}
                    />
                    <MetricRow
                      label="Pain 4w"
                      refValue={ref?.pain_count_last_4w ?? null}
                      cmpValue={cmp?.pain_count_last_4w ?? null}
                      delta={deltas?.pain_count_last_4w}
                    />
                  </section>

                  {/* Sparklines side by side */}
                  {ref?.sparkline && cmp?.sparkline && (
                    <section>
                      <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                        Sparklines
                      </div>
                      <div className="grid grid-cols-2 gap-4">
                        <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-3">
                          <div className="text-[10px] text-stone-400 mb-1">Ref</div>
                          <SparklineSVG data={ref.sparkline} />
                        </div>
                        <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-3">
                          <div className="text-[10px] text-stone-400 mb-1">Cmp</div>
                          <SparklineSVG data={cmp.sparkline} />
                        </div>
                      </div>
                    </section>
                  )}

                  {/* Velocity side-by-side */}
                  {ref?.velocity && ref.velocity.length > 0 && cmp?.velocity && cmp.velocity.length > 0 && (
                    <section>
                      <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                        Velocity
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-2">
                          <div className="text-[10px] text-stone-400 mb-1">Ref</div>
                          <VelocityChart
                            velocity={{
                              cluster_id: ref.cluster_id,
                              cluster_run_id: refRunId ?? 0,
                              median_pain_count: null,
                              weeks: ref.velocity,
                              windows: { last_4w_start: null, prior_4w_start: null, prior_4w_end: null },
                            }}
                            corpus={null}
                          />
                        </div>
                        <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-2">
                          <div className="text-[10px] text-stone-400 mb-1">Cmp</div>
                          <VelocityChart
                            velocity={{
                              cluster_id: cmp.cluster_id,
                              cluster_run_id: cmpRunId ?? 0,
                              median_pain_count: null,
                              weeks: cmp.velocity,
                              windows: { last_4w_start: null, prior_4w_start: null, prior_4w_end: null },
                            }}
                            corpus={null}
                          />
                        </div>
                      </div>
                    </section>
                  )}
                </>
              )}

              {/* ── Analysis Tab ── */}
              {tab === 'analysis' && (
                <>
                  <section className="space-y-2">
                    <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-1">
                      Path C Axes
                    </div>
                    <MetricRow
                      label="Q axis"
                      refValue={ref?.q_axis ?? null}
                      cmpValue={cmp?.q_axis ?? null}
                      fmt={(v) => (v !== null ? v.toFixed(3) : '—')}
                      delta={undefined}
                    />
                    <MetricRow
                      label="M axis"
                      refValue={ref?.m_axis ?? null}
                      cmpValue={cmp?.m_axis ?? null}
                      fmt={(v) => (v !== null ? v.toFixed(3) : '—')}
                      delta={undefined}
                    />
                    <MetricRow
                      label="S axis (comp)"
                      refValue={ref?.competition_adjusted_s_axis ?? null}
                      cmpValue={cmp?.competition_adjusted_s_axis ?? null}
                      fmt={(v) => (v !== null ? v.toFixed(3) : '—')}
                      delta={deltas?.competition_adjusted_s_axis}
                    />
                  </section>

                  <section className="space-y-2">
                    <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-1">
                      Ranking
                    </div>
                    <MetricRow
                      label="Pareto"
                      refValue={ref?.competition_adjusted_pareto_layer ?? null}
                      cmpValue={cmp?.competition_adjusted_pareto_layer ?? null}
                      fmt={fmtLayer}
                      delta={deltas?.competition_adjusted_pareto_layer}
                    />
                    <MetricRow
                      label="Balance"
                      refValue={ref?.competition_adjusted_balance_score ?? null}
                      cmpValue={cmp?.competition_adjusted_balance_score ?? null}
                      fmt={fmtPercent}
                      delta={deltas?.competition_adjusted_balance_score}
                    />
                    <MetricRow
                      label="Legacy Pareto"
                      refValue={ref?.pareto_layer ?? null}
                      cmpValue={cmp?.pareto_layer ?? null}
                      fmt={fmtLayer}
                      delta={deltas?.pareto_layer}
                    />
                    <MetricRow
                      label="Legacy Balance"
                      refValue={ref?.balance_score ?? null}
                      cmpValue={cmp?.balance_score ?? null}
                      fmt={fmtPercent}
                      delta={deltas?.balance_score}
                    />
                  </section>

                  <section className="space-y-2">
                    <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-1">
                      Eligibility &amp; Burst
                    </div>
                    <MetricRow
                      label="Eligible"
                      refValue={ref?.is_eligible ? 1 : 0}
                      cmpValue={cmp?.is_eligible ? 1 : 0}
                      fmt={(v) => (v === 1 ? '✓' : v === 0 ? '✗' : '—')}
                      delta={undefined}
                    />
                    {ref?.burst && cmp?.burst && (
                      <div className="grid grid-cols-[1fr_2fr_1fr_2fr] gap-2 items-center text-xs">
                        <div className="text-stone-500 text-[11px] uppercase tracking-[0.05em]">Burst</div>
                        <div className="flex items-center gap-1">
                          <span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${
                              ref.burst.label === 'Bursting'
                                ? 'bg-rose-50 text-rose-700 ring-rose-200'
                                : ref.burst.label === 'Low-volume spike'
                                  ? 'bg-amber-50 text-amber-700 ring-amber-200'
                                  : 'bg-stone-50 text-stone-500 ring-stone-200'
                            }`}
                          >
                            {ref.burst.label ?? '—'}
                          </span>
                        </div>
                        <div />
                        <div className="flex items-center gap-1">
                          <span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${
                              cmp.burst.label === 'Bursting'
                                ? 'bg-rose-50 text-rose-700 ring-rose-200'
                                : cmp.burst.label === 'Low-volume spike'
                                  ? 'bg-amber-50 text-amber-700 ring-amber-200'
                                  : 'bg-stone-50 text-stone-500 ring-stone-200'
                            }`}
                          >
                            {cmp.burst.label ?? '—'}
                          </span>
                        </div>
                      </div>
                    )}
                  </section>

                  {/* Dimensions diff */}
                  {ref?.dimensions && cmp?.dimensions && (
                    <section>
                      <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                        Dimension shifts
                      </div>
                      <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-3">
                        <div className="grid grid-cols-[1fr_auto_1fr] gap-x-3 gap-y-1 text-[11px]">
                          {Object.keys(ref.dimensions).map((key) => {
                            const dk = key as DimensionKey
                            const rv = ref.dimensions[dk]?.value
                            const cv = cmp.dimensions[dk]?.value
                            const diff =
                              rv !== null && cv !== null
                                ? Number(cv) - Number(rv)
                                : null
                            const diffColor =
                              diff === null
                                ? 'text-stone-300'
                                : diff > 0
                                  ? 'text-emerald-600'
                                  : diff < 0
                                    ? 'text-rose-500'
                                    : 'text-stone-400'
                            return (
                              <div key={key} className="contents">
                                <div className="text-stone-500 truncate">{key}</div>
                                <div className={`tabular-nums ${diffColor}`}>
                                  {diff !== null
                                    ? `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}`
                                    : '—'}
                                </div>
                                <div className="text-stone-400 text-right tabular-nums">
                                  {rv !== null ? rv.toFixed(2) : '—'} →{' '}
                                  {cv !== null ? cv.toFixed(2) : '—'}
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    </section>
                  )}
                </>
              )}

              {/* ── Market Tab ── */}
              {tab === 'market' && (
                <>
                  <section className="space-y-2">
                    <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-1">
                      Market Structure
                    </div>
                    <MetricRow
                      label="Structure"
                      refValue={undefined as any}
                      cmpValue={undefined as any}
                      fmt={() => fmtMarket(deltas?.market_structure_ref ?? null)}
                      delta={undefined}
                    />
                    <div className="grid grid-cols-[1fr_2fr_1fr_2fr] gap-2 items-center text-sm">
                      <div />
                      <div />
                      <div className="text-center">
                        {deltas?.market_structure_changed ? (
                          <span className="text-xs font-medium text-amber-600">changed →</span>
                        ) : (
                          <span className="text-xs text-stone-300">unchanged</span>
                        )}
                      </div>
                      <div className="text-stone-800">{fmtMarket(deltas?.market_structure_cmp ?? null)}</div>
                    </div>
                    <MetricRow
                      label="Ext. Gap"
                      refValue={ref?.external_supply_gap ?? null}
                      cmpValue={cmp?.external_supply_gap ?? null}
                      fmt={fmtPercent}
                      delta={deltas?.external_supply_gap}
                    />
                    <div className="grid grid-cols-[1fr_2fr_1fr_2fr] gap-2 items-center text-sm">
                      <div className="text-stone-500 text-[11px] uppercase tracking-[0.05em]">
                        Direct comps
                      </div>
                      <div className="text-stone-800 tabular-nums">
                        {ref?.competition?.direct_competitor_count ?? '—'}
                      </div>
                      <div className="text-center" />
                      <div className="text-stone-800 tabular-nums">
                        {cmp?.competition?.direct_competitor_count ?? '—'}
                      </div>
                    </div>
                  </section>

                  {/* Competitor diff */}
                  {(deltas?.new_competitors?.length ?? 0) > 0 ||
                  (deltas?.dropped_competitors?.length ?? 0) > 0 ? (
                    <section>
                      <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-2">
                        Competitor changes
                      </div>
                      <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-3 space-y-2">
                        {deltas && deltas.new_competitors.length > 0 && (
                          <div>
                            <div className="text-[10px] text-emerald-600 font-medium mb-1">
                              + New ({deltas.new_competitors.length})
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {deltas.new_competitors.map((name) => (
                                <span
                                  key={name}
                                  className="rounded-full bg-emerald-50 text-emerald-700 text-[11px] px-2 py-0.5 ring-1 ring-emerald-200"
                                >
                                  {name}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                        {deltas && deltas.dropped_competitors.length > 0 && (
                          <div>
                            <div className="text-[10px] text-rose-500 font-medium mb-1">
                              − Dropped ({deltas.dropped_competitors.length})
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {deltas.dropped_competitors.map((name) => (
                                <span
                                  key={name}
                                  className="rounded-full bg-rose-50 text-rose-600 text-[11px] px-2 py-0.5 ring-1 ring-rose-200"
                                >
                                  {name}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </section>
                  ) : (
                    <div className="text-xs text-stone-400">No competitor changes detected.</div>
                  )}

                  {/* Competition rationales */}
                  {(ref?.competition?.rationale || cmp?.competition?.rationale) && (
                    <section className="space-y-2">
                      <div className="text-[11px] uppercase tracking-[0.08em] text-stone-500 mb-1">
                        Rationale
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-3">
                          <div className="text-[10px] text-stone-400 mb-1">Ref</div>
                          <div className="text-xs text-stone-700 leading-relaxed">
                            {ref?.competition?.rationale || '—'}
                          </div>
                        </div>
                        <div className="rounded-xl bg-white/60 ring-1 ring-stone-200/60 p-3">
                          <div className="text-[10px] text-stone-400 mb-1">Cmp</div>
                          <div className="text-xs text-stone-700 leading-relaxed">
                            {cmp?.competition?.rationale || '—'}
                          </div>
                        </div>
                      </div>
                    </section>
                  )}
                </>
              )}
            </div>
          </>
        )}

        {!loading && !error && !data && (
          <div className="px-6 py-8 text-sm text-stone-400 text-center">
            Select a cluster and two runs to compare.
          </div>
        )}
      </div>
    </>
  )
}

/** Tiny inline SVG sparkline component. */
function SparklineSVG({ data }: { data: Array<number | null> }) {
  const w = 120
  const h = 24
  const valid = data.filter((v): v is number => v !== null)
  if (valid.length < 2) {
    return (
      <svg width={w} height={h} className="block">
        <text x={w / 2} y={h / 2} textAnchor="middle" className="fill-stone-300 text-[8px]">
          no data
        </text>
      </svg>
    )
  }
  const max = Math.max(...valid, 1)
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * (w - 4) + 2
    const y = v !== null ? h - 2 - ((v / max) * (h - 4)) : null
    return { x, y }
  })
  const pathParts: string[] = []
  let first = true
  for (const p of points) {
    if (p.y === null) {
      first = true
      continue
    }
    if (first) {
      pathParts.push(`M${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      first = false
    } else {
      pathParts.push(`L${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    }
  }
  return (
    <svg width={w} height={h} className="block">
      <path
        d={pathParts.join(' ')}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        className="text-stone-400"
      />
    </svg>
  )
}
