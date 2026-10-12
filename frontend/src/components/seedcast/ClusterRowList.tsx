import { useState } from 'react'
import { formatCount } from '../../lib/seedcast/format'
import type { SeedcastCluster } from '../../lib/seedcast/types'
import Pill from './Pill'
import Sparkline from './Sparkline'

export interface RowBadge {
  text: string
  color: string
}

interface Props {
  clusters: SeedcastCluster[]
  /** Rank shown for the first row (Movers continues from 11). */
  startRank?: number
  step?: number
  badge: (c: SeedcastCluster) => RowBadge
}

/** Scrollable list with a "Show more" button. Shared by Landscape and Movers. */
export default function ClusterRowList({ clusters, startRank = 1, step = 12, badge }: Props) {
  const [count, setCount] = useState(step)
  const shown = clusters.slice(0, count)
  return (
    <section className="mt-10 rounded-[18px] border border-seed-line bg-white px-6 pb-6 pt-2">
      {shown.map((c, i) => {
        const b = badge(c)
        return (
          <a
            key={c.id}
            href={`#/cluster/${c.id}`}
            className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-[#F0EAF8] py-[18px] text-seed-ink no-underline"
          >
            <span className="w-9 shrink-0 font-seedmono text-sm text-seed-muted">{startRank + i}</span>
            <span className="flex min-w-0 flex-[1_1_320px] flex-col gap-[5px]">
              <span className="flex flex-wrap items-center gap-x-2.5 gap-y-2">
                <span className="text-lg font-bold">{c.name}</span>
                <Pill size="sm">{c.audience}</Pill>
                {c.bursting && <Pill size="sm" tone="burst">Bursting</Pill>}
              </span>
              <span className="text-sm leading-snug text-seed-muted">{c.statement}</span>
              <span className="font-seedmono text-xs text-seed-muted">
                {c.topCommunity} · {c.communities} communities · since {c.firstSeen}
              </span>
            </span>
            <Sparkline values={c.series} color={b.color} />
            <span className="w-[76px] shrink-0 text-right">
              <span className="block font-seedmono text-[17px] font-medium">{formatCount(c.posts4w)}</span>
              <span className="text-xs text-seed-muted">4 weeks</span>
            </span>
            <span className="w-[76px] shrink-0 text-right">
              <span className="block font-seedmono text-[17px] font-medium">{formatCount(c.total)}</span>
              <span className="text-xs text-seed-muted">total</span>
            </span>
            <span className="w-[100px] shrink-0 text-right font-seedmono text-[15px] font-medium" style={{ color: b.color }}>
              {b.text}
            </span>
          </a>
        )
      })}
      {count < clusters.length && (
        <div className="flex justify-center pt-5">
          <button
            type="button"
            onClick={() => setCount((n) => n + step)}
            className="min-h-[48px] cursor-pointer rounded-full border-[1.5px] border-seed-accent bg-white px-7 text-[15px] font-semibold text-seed-accent"
          >
            Show more
          </button>
        </div>
      )}
    </section>
  )
}
