import { useState } from 'react'
import type { AxisWeights, DimensionGroup, ScoreConfig, Weights } from '../types'
import { DEFAULT_AXIS_WEIGHTS } from '../types'
import { SCORE_DESCRIPTIONS } from '../lib/scoreDescriptions'

export interface WeightsPanelProps {
  // Legacy 14-dim weights
  config: ScoreConfig | null
  weights: Weights
  defaults: Weights
  onChange: (key: keyof Weights, value: number) => void
  onResetAll: () => void
  onResetGroup: (group: DimensionGroup) => void
  // Path C axis weights
  axisWeights: AxisWeights
  onAxisChange: (w: AxisWeights) => void
}

const GROUP_ORDER: DimensionGroup[] = ['quality', 'viability', 'velocity']
const GROUP_LABELS: Record<DimensionGroup, string> = {
  quality: 'Quality',
  viability: 'Viability (LLM)',
  velocity: 'Velocity',
}

const AXIS_CONFIG: Array<{
  key: keyof AxisWeights
  label: string
  color: string
  barColor: string
  description: string
}> = [
  {
    key: 'q',
    label: 'Quality (Q)',
    color: 'accent-violet-600',
    barColor: 'bg-violet-500',
    description: 'Signal quality: coherence, diversity, specificity, temporal spread.',
  },
  {
    key: 'm',
    label: 'Momentum (M)',
    color: 'accent-sky-600',
    barColor: 'bg-sky-500',
    description: 'Growth momentum: long-term slope + prior-4w volume.',
  },
  {
    key: 's',
    label: 'Supply gap (S)',
    color: 'accent-emerald-600',
    barColor: 'bg-emerald-500',
    description: 'Competitive supply gap: gap signal, tool density, workaround ratio.',
  },
]

export default function WeightsPanel({
  config,
  weights,
  defaults,
  onChange,
  onResetAll,
  onResetGroup,
  axisWeights,
  onAxisChange,
}: WeightsPanelProps) {
  const [open, setOpen] = useState<Record<DimensionGroup, boolean>>({
    quality: true,
    viability: true,
    velocity: true,
  })
  const [axisOpen, setAxisOpen] = useState(true)

  const axisTotal = axisWeights.q + axisWeights.m + axisWeights.s

  function setAxis(key: keyof AxisWeights, v: number) {
    onAxisChange({ ...axisWeights, [key]: v })
  }

  function resetAxis() {
    onAxisChange({ ...DEFAULT_AXIS_WEIGHTS })
  }

  const byGroup: Record<DimensionGroup, NonNullable<typeof config>['dimensions']> = {
    quality: [],
    viability: [],
    velocity: [],
  }
  if (config) {
    for (const d of config.dimensions) byGroup[d.group].push(d)
  }

  return (
    <aside className="flex flex-col h-full w-72 shrink-0 bg-[#FBF8F3] ring-1 ring-stone-200/70 rounded-xl shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-stone-200/60 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-stone-900 tracking-tight">
          Score weights
        </h2>
        <button
          type="button"
          onClick={onResetAll}
          className="text-xs text-stone-500 hover:text-stone-900"
        >
          Reset all
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2">

        {/* ── PATH C: Explore weights ── */}
        <div className="mb-1">
          <div className="flex items-center justify-between px-1 py-1">
            <button
              type="button"
              onClick={() => setAxisOpen((o) => !o)}
              className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-violet-600 hover:text-violet-900"
            >
              <span className="inline-block w-3 text-stone-400">
                {axisOpen ? '▾' : '▸'}
              </span>
              Explore (Q / M / S)
            </button>
            <button
              type="button"
              onClick={resetAxis}
              className="text-[10px] text-stone-400 hover:text-stone-700"
              title="Reset axis weights"
            >
              reset
            </button>
          </div>

          {axisOpen && (
            <div className="flex flex-col gap-3 pl-4 pr-1 pt-1 pb-3 border-b border-stone-100 mb-2">
              {AXIS_CONFIG.map(({ key, label, color, barColor, description }) => {
                const w = axisWeights[key]
                const pct = axisTotal === 0 ? 0 : w / axisTotal
                return (
                  <div key={key} className="flex flex-col gap-0.5" title={description}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-stone-700 font-medium">{label}</span>
                      <span className="tabular-nums text-stone-500">{w.toFixed(2)}</span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.01}
                      value={w}
                      onChange={(e) => setAxis(key, Number(e.target.value))}
                      className={`w-full h-1.5 rounded-full ${color}`}
                      aria-label={label}
                    />
                    {/* share bar */}
                    <div className="flex items-center gap-2 mt-0.5">
                      <div className="flex-1 h-1 bg-stone-100 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${barColor}`}
                          style={{ width: `${pct * 100}%` }}
                        />
                      </div>
                      <span className="text-[10px] tabular-nums text-stone-400 w-7 text-right">
                        {(pct * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                )
              })}
              <div className="text-[10px] text-stone-400 leading-snug mt-1">
                Controls the <span className="text-violet-500 font-medium">Explore ✦</span> column.
                Does not affect Pareto Layer or Balance.
              </div>
            </div>
          )}
        </div>

        {/* ── LEGACY: per-dimension weights ── */}
        {config ? (
          GROUP_ORDER.map((g) => {
            const dims = byGroup[g]
            if (dims.length === 0) return null
            const expanded = open[g]
            return (
              <div key={g} className="mb-3">
                <div className="flex items-center justify-between px-1 py-1">
                  <button
                    type="button"
                    onClick={() => setOpen((o) => ({ ...o, [g]: !o[g] }))}
                    className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-stone-600 hover:text-stone-900"
                  >
                    <span className="inline-block w-3 text-stone-400">
                      {expanded ? '▾' : '▸'}
                    </span>
                    {GROUP_LABELS[g]}
                  </button>
                  <button
                    type="button"
                    onClick={() => onResetGroup(g)}
                    className="text-[10px] text-stone-400 hover:text-stone-700"
                    title={`Reset ${GROUP_LABELS[g]} weights`}
                  >
                    reset
                  </button>
                </div>
                {expanded && (
                  <div className="flex flex-col gap-1.5 pl-4 pr-1 pt-1">
                    {dims.map((d) => {
                      const w = weights[d.key] ?? 0
                      const isDefault = w === defaults[d.key]
                      return (
                        <div
                          key={d.key}
                          className="flex flex-col gap-0.5"
                          title={SCORE_DESCRIPTIONS[d.key]}
                        >
                          <div className="flex items-center justify-between text-xs">
                            <span className="text-stone-700 truncate pr-1">
                              {d.label}
                            </span>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <span className="tabular-nums text-stone-500 w-7 text-right">
                                {w.toFixed(1)}
                              </span>
                              <button
                                type="button"
                                onClick={() => onChange(d.key, defaults[d.key])}
                                disabled={isDefault}
                                className="text-stone-400 hover:text-stone-700 disabled:opacity-30 text-xs leading-none"
                                title="Reset to default"
                              >
                                ↻
                              </button>
                            </div>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={1}
                            step={0.1}
                            value={w}
                            onChange={(e) => onChange(d.key, Number(e.target.value))}
                            className="w-full accent-indigo-600 h-1"
                          />
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })
        ) : (
          <div className="px-1 py-2 text-sm text-stone-500">Loading score config…</div>
        )}
      </div>
    </aside>
  )
}
