import { useMemo, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { CorpusVelocityResponse, VelocityResponse } from '../types'

export type VelocityView = 'volume_lift' | 'recent_trend' | 'raw' | 'share_global' | 'log'

export interface VelocityChartProps {
  velocity: VelocityResponse
  corpus: CorpusVelocityResponse | null
  highlight?: boolean
}

const STONE_700 = '#44403c'
const STONE_300 = '#d6d3d1'
const STONE_500 = '#78716c'
const EMERALD_200 = '#a7f3d0'
const LIFT_MIN_POINTS = 2
const LOW_VOLUME_MIN_POINTS = 3
const LOW_VOLUME_MIN_TOTAL_PAIN = 6

interface ChartRow {
  week_start: string
  cluster_count: number | null
  effective_pain_count: number | null
  pain_count: number | null
  pain_count_partial: number | null
  share_global: number | null
  share_global_partial: number | null
  cluster_volume_lift: number | null
  cluster_volume_lift_partial: number | null
  corpus_volume_lift: number | null
  corpus_volume_lift_partial: number | null
  cluster_relative_lift: number | null
  cluster_relative_lift_partial: number | null
  corpus_total_value: number | null
  corpus_total: number | null
  corpus_total_partial: number | null
  is_partial: boolean
}

type ChartNumberKey = {
  [K in keyof ChartRow]: ChartRow[K] extends number | null ? K : never
}[keyof ChartRow]

function buildRows(
  v: VelocityResponse,
  corpus: CorpusVelocityResponse | null,
): ChartRow[] {
  const corpusByWeek = new Map<string, { total: number | null; partial: boolean }>()
  if (corpus) {
    for (const w of corpus.weeks) {
      corpusByWeek.set(w.week_start, { total: w.total_pain, partial: w.is_partial })
    }
  }
  // Find first partial index so a connecting segment can be drawn (last solid + partial points).
  let firstPartialIdx = v.weeks.findIndex((w) => w.is_partial)
  if (firstPartialIdx < 0) firstPartialIdx = v.weeks.length

  const rows = v.weeks.map((w, i) => {
    const c = corpusByWeek.get(w.week_start) ?? { total: null, partial: false }
    const inPartialSegment = i >= firstPartialIdx - 1 // include last solid for continuity
    const isPartial = w.is_partial
    const clusterCount = typeof w.pain_count === 'number' ? w.pain_count : null
    const corpusTotal = typeof c.total === 'number' ? c.total : null
    const effectivePainCount = clusterCount ?? (corpusTotal !== null ? 0 : null)
    return {
      week_start: w.week_start,
      cluster_count: clusterCount ?? (corpusTotal !== null ? 0 : null),
      effective_pain_count: effectivePainCount,
      pain_count: !isPartial ? clusterCount : null,
      pain_count_partial: inPartialSegment ? clusterCount : null,
      share_global: !isPartial ? w.share_global : null,
      share_global_partial: inPartialSegment ? w.share_global : null,
      cluster_volume_lift: null,
      cluster_volume_lift_partial: null,
      corpus_volume_lift: null,
      corpus_volume_lift_partial: null,
      cluster_relative_lift: null,
      cluster_relative_lift_partial: null,
      corpus_total_value: corpusTotal,
      corpus_total: !c.partial ? corpusTotal : null,
      corpus_total_partial: inPartialSegment ? corpusTotal : null,
      is_partial: isPartial,
    }
  })

  const baselineIndex = rows.findIndex(
    (r) =>
      !r.is_partial &&
      typeof r.effective_pain_count === 'number' &&
      r.effective_pain_count > 0 &&
      typeof r.corpus_total_value === 'number' &&
      r.corpus_total_value > 0,
  )
  if (baselineIndex < 0) return rows

  const baselineRow = rows[baselineIndex]
  const clusterBaseline = baselineRow.effective_pain_count
  const corpusBaseline = baselineRow.corpus_total_value
  if (!clusterBaseline || !corpusBaseline) return rows
  const shareBaseline = clusterBaseline / corpusBaseline

  return rows.map((r, i) => {
    const canCompute =
      i >= baselineIndex &&
      typeof r.effective_pain_count === 'number' &&
      typeof r.corpus_total_value === 'number' &&
      r.corpus_total_value > 0
    const clusterVolumeLift = canCompute
      ? ((r.effective_pain_count! - clusterBaseline) / clusterBaseline) * 100
      : null
    const corpusVolumeLift = canCompute
      ? ((r.corpus_total_value! - corpusBaseline) / corpusBaseline) * 100
      : null
    const share = canCompute ? r.effective_pain_count! / r.corpus_total_value! : null
    const relativeLift = share === null ? null : ((share - shareBaseline) / shareBaseline) * 100
    const inPartialSegment = i >= firstPartialIdx - 1 && i >= baselineIndex
    return {
      ...r,
      cluster_volume_lift: !r.is_partial ? clusterVolumeLift : null,
      cluster_volume_lift_partial: inPartialSegment ? clusterVolumeLift : null,
      corpus_volume_lift: !r.is_partial ? corpusVolumeLift : null,
      corpus_volume_lift_partial: inPartialSegment ? corpusVolumeLift : null,
      cluster_relative_lift: !r.is_partial ? relativeLift : null,
      cluster_relative_lift_partial: inPartialSegment ? relativeLift : null,
    }
  })
}

function formatCount(v: number | null): string {
  return typeof v === 'number' ? v.toLocaleString() : '—'
}

function formatLift(v: number | null): string {
  if (typeof v !== 'number') return '—'
  const rounded = Math.abs(v) < 0.5 ? 0 : v
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(0)}%`
}

function coalesceNumber(...values: Array<number | null>): number | null {
  return values.find((v): v is number => typeof v === 'number') ?? null
}

function liftDomain(rows: ChartRow[], keys: ChartNumberKey[]): [number, number] {
  const values = rows.flatMap((r) =>
    keys
      .map((key) => r[key])
      .filter((v): v is number => typeof v === 'number' && Number.isFinite(v)),
  )
  if (!values.length) return [-100, 100]
  const min = Math.min(0, ...values)
  const max = Math.max(0, ...values)
  const span = Math.max(20, max - min)
  const pad = span * 0.15
  return [Math.floor((min - pad) / 10) * 10, Math.ceil((max + pad) / 10) * 10]
}

function formatWeek(s: string): string {
  // 2026-04-13 → Apr 13
  const d = new Date(s + 'T00:00:00Z')
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}

export default function VelocityChart({ velocity, corpus, highlight }: VelocityChartProps) {
  const [view, setView] = useState<VelocityView>('volume_lift')

  const rows = useMemo(() => buildRows(velocity, corpus), [velocity, corpus])
  const volumeLiftRows = rows.filter((r) => typeof r.cluster_volume_lift === 'number')
  const trendRows = rows.filter((r) => typeof r.cluster_relative_lift === 'number')
  const nonPartialRows = rows.filter((r) => !r.is_partial)
  const positiveWeeks = nonPartialRows.filter((r) => (r.effective_pain_count ?? 0) > 0).length
  const trendPainTotal = nonPartialRows.reduce(
    (acc, r) => acc + (r.effective_pain_count ?? 0),
    0,
  )
  const volumeLiftAvailable = volumeLiftRows.length >= LIFT_MIN_POINTS
  const trendAvailable = trendRows.length >= LIFT_MIN_POINTS
  const lowVolume =
    positiveWeeks < LOW_VOLUME_MIN_POINTS || trendPainTotal < LOW_VOLUME_MIN_TOTAL_PAIN
  const showVolumeLift = view === 'volume_lift'
  const showTrend = view === 'recent_trend'
  const showRaw = view === 'raw'
  const showShare = view === 'share_global'
  const useLog = view === 'log'
  const isLiftView = showVolumeLift || showTrend
  const unavailable =
    (showVolumeLift && !volumeLiftAvailable) || (showTrend && !trendAvailable)

  const windows = velocity.windows
  const lastWeek = velocity.weeks[velocity.weeks.length - 1]?.week_start

  const primaryKey = showVolumeLift
    ? 'cluster_volume_lift'
    : showTrend
      ? 'cluster_relative_lift'
      : showShare
        ? 'share_global'
        : 'pain_count'
  const primaryKeyPartial = showTrend
    ? 'cluster_relative_lift_partial'
    : showVolumeLift
      ? 'cluster_volume_lift_partial'
      : showShare
        ? 'share_global_partial'
        : 'pain_count_partial'
  const primaryLineType = isLiftView ? 'monotone' : 'linear'
  const primaryDot = isLiftView ? false : { r: 2.5, fill: STONE_700 }

  const yScale = useLog ? 'log' : 'linear'
  const liftKeys: ChartNumberKey[] = showVolumeLift
    ? [
        'cluster_volume_lift',
        'cluster_volume_lift_partial',
        'corpus_volume_lift',
        'corpus_volume_lift_partial',
      ]
    : showTrend
      ? [
          'cluster_relative_lift',
          'cluster_relative_lift_partial',
          'corpus_volume_lift',
          'corpus_volume_lift_partial',
        ]
      : []
  // Recharts log scale requires positive numbers; provide a tiny floor as domain min.
  const yDomain: [number | string, number | string] = useLog
    ? [showShare ? 0.0001 : 1, 'auto']
    : isLiftView
      ? liftDomain(rows, liftKeys)
      : [0, 'auto']

  const bandOpacity = highlight ? 0.28 : 0.1
  const lowVolumeLabel = (showVolumeLift || showTrend) && !unavailable && lowVolume

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <div className="inline-flex rounded-lg ring-1 ring-stone-200 bg-stone-50 p-0.5 text-xs">
          {(
            [
              { key: 'volume_lift', label: 'Volume lift' },
              { key: 'recent_trend', label: 'Recent trend' },
              { key: 'raw', label: 'Raw' },
              { key: 'share_global', label: 'Share global' },
              { key: 'log', label: 'Log scale' },
            ] as Array<{ key: VelocityView; label: string }>
          ).map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => setView(opt.key)}
              className={`px-2.5 py-1 rounded-md transition ${
                view === opt.key
                  ? 'bg-white text-stone-900 shadow-sm ring-1 ring-stone-200'
                  : 'text-stone-500 hover:text-stone-800'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        {velocity.median_pain_count != null && (showRaw || useLog) && (
          <div className="text-[11px] text-stone-400 ml-2">
            median {velocity.median_pain_count.toFixed(0)}
          </div>
        )}
        {lowVolumeLabel && <div className="text-[11px] text-amber-600 ml-2">low volume</div>}
      </div>

      <div style={{ width: '100%', height: 220 }}>
        {unavailable ? (
          <div className="h-full rounded-lg ring-1 ring-stone-200/70 bg-stone-50/50 flex items-center justify-center text-sm text-stone-400">
            {showTrend ? 'insufficient recent volume' : 'insufficient volume'}
          </div>
        ) : (
          <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e7e5e4" vertical={false} />
            <XAxis
              dataKey="week_start"
              tickFormatter={formatWeek}
              tick={{ fontSize: 10, fill: STONE_500 }}
              stroke="#d6d3d1"
              minTickGap={32}
            />
            <YAxis
              yAxisId="left"
              scale={yScale as 'log' | 'linear'}
              domain={yDomain}
              allowDataOverflow
              tick={{ fontSize: 10, fill: STONE_500 }}
              stroke="#d6d3d1"
              width={42}
              tickFormatter={(v: number) =>
                isLiftView
                  ? formatLift(v)
                  : showShare
                    ? `${(v * 100).toFixed(1)}%`
                    : String(v)
              }
            />
            {showRaw && (
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 10, fill: STONE_300 }}
                stroke="#e7e5e4"
                width={42}
              />
            )}
            <Tooltip
              labelFormatter={(label) => formatWeek(String(label))}
              contentStyle={{
                background: 'white',
                border: '1px solid #e7e5e4',
                borderRadius: 8,
                fontSize: 11,
              }}
              content={({ active, label, payload }) => {
                if (!active || !payload?.length) return null
                const row = payload[0].payload as ChartRow
                const clusterVolumeLift = coalesceNumber(
                  row.cluster_volume_lift,
                  row.cluster_volume_lift_partial,
                )
                const corpusVolumeLift = coalesceNumber(
                  row.corpus_volume_lift,
                  row.corpus_volume_lift_partial,
                )
                const relativeLift = coalesceNumber(
                  row.cluster_relative_lift,
                  row.cluster_relative_lift_partial,
                )
                const shareGlobal = coalesceNumber(row.share_global, row.share_global_partial)
                const rawPain = coalesceNumber(row.pain_count, row.pain_count_partial)
                const rawCorpus = coalesceNumber(row.corpus_total, row.corpus_total_partial)
                const itemClass = 'flex justify-between gap-6'
                return (
                  <div className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-[11px] text-stone-600 shadow-sm min-w-44">
                    <div className="mb-1 font-medium text-stone-900">{formatWeek(String(label))}</div>
                    {showVolumeLift && (
                      <>
                        <div className={itemClass}>
                          <span>Cluster volume lift</span>
                          <span className="font-medium text-stone-900">
                            {formatLift(clusterVolumeLift)}
                          </span>
                        </div>
                        <div className={itemClass}>
                          <span>Corpus volume lift</span>
                          <span>{formatLift(corpusVolumeLift)}</span>
                        </div>
                      </>
                    )}
                    {showTrend && (
                      <>
                        <div className={itemClass}>
                          <span>Cluster relative lift</span>
                          <span className="font-medium text-stone-900">
                            {formatLift(relativeLift)}
                          </span>
                        </div>
                        <div className={itemClass}>
                          <span>Corpus volume lift</span>
                          <span>{formatLift(corpusVolumeLift)}</span>
                        </div>
                      </>
                    )}
                    {showShare && (
                      <div className={itemClass}>
                        <span>Share global</span>
                        <span className="font-medium text-stone-900">
                          {typeof shareGlobal === 'number'
                            ? `${(shareGlobal * 100).toFixed(2)}%`
                            : '—'}
                        </span>
                      </div>
                    )}
                    {(showRaw || useLog) && (
                      <>
                        <div className={itemClass}>
                          <span>Cluster pain count</span>
                          <span className="font-medium text-stone-900">
                            {formatCount(rawPain ?? row.cluster_count)}
                          </span>
                        </div>
                        {showRaw && (
                          <div className={itemClass}>
                            <span>Corpus background</span>
                            <span>{formatCount(rawCorpus ?? row.corpus_total_value)}</span>
                          </div>
                        )}
                      </>
                    )}
                    {isLiftView && (
                      <div className="mt-1 border-t border-stone-100 pt-1 text-stone-400">
                        count {formatCount(row.cluster_count)} / corpus{' '}
                        {formatCount(row.corpus_total_value)}
                      </div>
                    )}
                  </div>
                )
              }}
            />

            {/* Reference window areas */}
            {windows.prior_4w_start && windows.prior_4w_end && (
              <ReferenceArea
                yAxisId="left"
                x1={windows.prior_4w_start}
                x2={windows.prior_4w_end}
                fill={STONE_300}
                fillOpacity={bandOpacity}
                stroke="none"
              />
            )}
            {windows.last_4w_start && lastWeek && (
              <ReferenceArea
                yAxisId="left"
                x1={windows.last_4w_start}
                x2={lastWeek}
                fill={EMERALD_200}
                fillOpacity={bandOpacity + 0.05}
                stroke="none"
              />
            )}

            {(showVolumeLift || showTrend) && (
              <>
                <ReferenceLine
                  yAxisId="left"
                  y={0}
                  stroke={STONE_500}
                  strokeDasharray="2 4"
                  strokeOpacity={0.45}
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="corpus_volume_lift"
                  stroke={STONE_300}
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls={false}
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="corpus_volume_lift_partial"
                  stroke={STONE_300}
                  strokeWidth={1.5}
                  strokeDasharray="3 3"
                  dot={false}
                  isAnimationActive={false}
                  connectNulls={false}
                />
              </>
            )}

            {/* Median line (only in raw view) */}
            {(showRaw || useLog) && velocity.median_pain_count != null && (
              <ReferenceLine
                yAxisId="left"
                y={velocity.median_pain_count}
                stroke={STONE_500}
                strokeDasharray="2 4"
                strokeOpacity={0.5}
              />
            )}

            {/* Corpus line (raw only) */}
            {showRaw && (
              <>
                <Line
                  yAxisId="right"
                  type="linear"
                  dataKey="corpus_total"
                  stroke={STONE_300}
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls={false}
                />
                <Line
                  yAxisId="right"
                  type="linear"
                  dataKey="corpus_total_partial"
                  stroke={STONE_300}
                  strokeWidth={1.5}
                  strokeDasharray="3 3"
                  dot={false}
                  isAnimationActive={false}
                  connectNulls={false}
                />
              </>
            )}

            {/* Primary line */}
            <Line
              yAxisId="left"
              type={primaryLineType}
              dataKey={primaryKey}
              stroke={STONE_700}
              strokeWidth={2}
              dot={primaryDot}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
              connectNulls={false}
            />
            <Line
              yAxisId="left"
              type={primaryLineType}
              dataKey={primaryKeyPartial}
              stroke={STONE_700}
              strokeWidth={2}
              strokeDasharray="3 3"
              dot={{ r: 2, fill: STONE_700, fillOpacity: 0.5 }}
              isAnimationActive={false}
              connectNulls={false}
            />
          </LineChart>
        </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
