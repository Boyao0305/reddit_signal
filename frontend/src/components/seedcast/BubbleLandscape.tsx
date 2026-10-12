import { useMemo, useState } from 'react'
import { bubbleLabel, keywordOf, momentumOf } from '../../lib/seedcast/format'
import { packCircles } from '../../lib/seedcast/pack'
import { AUDIENCE_FILL, SEED, wordOn } from '../../lib/seedcast/theme'
import type { Audience, SeedcastCluster } from '../../lib/seedcast/types'
import ClusterCaption from './ClusterCaption'

type ColorMode = 'momentum' | 'audience'

const W = 1100
const H = 600

function fillFor(c: SeedcastCluster, mode: ColorMode): string {
  if (mode === 'audience') return AUDIENCE_FILL[c.audience]
  const m = momentumOf(c.ratio)
  return m === 'rising' ? SEED.accent : m === 'fading' ? SEED.fade : SEED.steady
}

const LEGEND: Record<ColorMode, Array<{ name: string; color: string }>> = {
  momentum: [
    { name: 'Rising', color: SEED.accent },
    { name: 'Fading', color: SEED.fade },
    { name: 'Flat or too small', color: SEED.steady },
  ],
  audience: (['B2B', 'B2C', 'Mixed', 'Unclear'] as Audience[]).map((a) => ({ name: a, color: AUDIENCE_FILL[a] })),
}

function ModeButton({ on, onClick, children }: { on: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`min-h-[44px] cursor-pointer rounded-full border-[1.5px] px-5 text-[15px] font-semibold ${on ? 'border-seed-accent bg-seed-accent text-white' : 'border-seed-line bg-transparent text-seed-ink'}`}
    >
      {children}
    </button>
  )
}

/** Packed-bubble view: circle area = posts in the last 4 weeks. */
export default function BubbleLandscape({ clusters }: { clusters: SeedcastCluster[] }) {
  const [mode, setMode] = useState<ColorMode>('momentum')
  const [hoverId, setHoverId] = useState<number | null>(null)

  const sorted = useMemo(() => clusters.slice().sort((a, b) => b.posts4w - a.posts4w), [clusters])
  const layout = useMemo(() => packCircles(sorted.map((c) => Math.sqrt(c.posts4w) * 3), { width: W, height: H }), [sorted])
  const active = sorted.find((c) => c.id === hoverId) ?? sorted[0]
  if (!active) return null

  return (
    <section className="relative rounded-[18px] border border-seed-line bg-white px-5 pb-3 pt-5 shadow-[0_16px_44px_rgba(58,22,112,0.16)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Color circles by" className="flex gap-2">
          <ModeButton on={mode === 'momentum'} onClick={() => setMode('momentum')}>
            Momentum
          </ModeButton>
          <ModeButton on={mode === 'audience'} onClick={() => setMode('audience')}>
            Audience
          </ModeButton>
        </div>
        <ul className="m-0 flex list-none flex-wrap gap-4 p-0 text-sm text-seed-muted">
          {LEGEND[mode].map((l) => (
            <li key={l.name} className="flex items-center gap-1.5">
              <span className="inline-block h-3.5 w-3.5 rounded-full" style={{ background: l.color }} />
              {l.name}
            </li>
          ))}
        </ul>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 block h-auto w-full" role="group" aria-label="Packed circle chart of pain clusters; circle area is pain posts in the last four weeks">
        {sorted.map((c, i) => {
          const p = layout[i]
          const fill = fillFor(c, mode)
          const label = bubbleLabel(keywordOf(c.name), p.r)
          return (
            <a
              key={c.id}
              href={`#/cluster/${c.id}`}
              aria-label={`${c.name}, ${c.posts4w} posts in the last 4 weeks`}
              onMouseEnter={() => setHoverId(c.id)}
              onFocus={() => setHoverId(c.id)}
            >
              <circle cx={p.x} cy={p.y} r={p.r} fill={fill} stroke="#fff" strokeWidth={c.id === active.id ? 3.5 : 2} />
              {label && (
                <g fontFamily="DM Sans, sans-serif" fontSize={label.fontSize} fontWeight={700} fill={wordOn(fill)} textAnchor="middle" pointerEvents="none">
                  {label.lines.map((line, k) => (
                    <text key={k} x={p.x} y={p.y + label.dy[k]}>
                      {line}
                    </text>
                  ))}
                </g>
              )}
            </a>
          )
        })}
      </svg>
      <ClusterCaption cluster={active} />
    </section>
  )
}
