import { formatCount, formatRatio, momentumOf } from '../../lib/seedcast/format'
import { SEED } from '../../lib/seedcast/theme'
import type { SeedcastCluster } from '../../lib/seedcast/types'
import Pill from './Pill'

function Stat({ value, label, span = 1, small = false, color }: { value: string; label: string; span?: 1 | 2; small?: boolean; color?: string }) {
  return (
    <div className={`bg-white p-3.5 ${span === 2 ? 'col-span-2' : ''}`}>
      <div className={`font-seedmono font-medium ${small ? 'py-[3px] text-lg' : 'text-[22px]'}`} style={color ? { color } : undefined}>
        {value}
      </div>
      <div className="text-xs text-seed-muted">{label}</div>
    </div>
  )
}

/** Detail strip shown under the bubble chart for the hovered cluster. */
export default function ClusterCaption({ cluster: c }: { cluster: SeedcastCluster }) {
  const rising = momentumOf(c.ratio) === 'rising'
  return (
    <div className="flex flex-wrap gap-x-10 gap-y-4 border-t border-seed-line px-2 pb-3 pt-5" aria-live="polite">
      <div className="flex min-w-0 flex-[1_1_380px] flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="text-[26px] font-extrabold tracking-[-0.01em]">{c.name}</span>
          <Pill>{c.audience}</Pill>
          {c.bursting && <Pill tone="burst">Bursting</Pill>}
        </div>
        <p className="m-0 text-base leading-normal text-seed-ink2">{c.statement}</p>
        {c.icp && <span className="text-sm text-seed-muted">Mostly affects {c.icp}</span>}
        <a href={`#/cluster/${c.id}`} className="py-2 text-[15px] font-bold text-seed-accent no-underline">
          Open →
        </a>
      </div>
      <div className="grid min-w-0 flex-[1_1_420px] grid-cols-4 gap-px self-start overflow-hidden rounded-xl border border-seed-line bg-seed-line">
        <Stat value={formatCount(c.posts4w)} label="posts, 4 weeks" />
        <Stat value={formatRatio(c.ratio)} label="share vs prior 4 weeks" color={rising ? SEED.accent : undefined} />
        <Stat value={formatCount(c.total)} label="posts, total" />
        <Stat value={String(c.communities)} label="communities" />
        <Stat value={`${c.workaroundPct}%`} label="use a workaround" />
        <Stat value={c.topCommunity} label="top community" span={2} small />
        <Stat value={c.firstSeen} label="first seen" small />
      </div>
    </div>
  )
}
