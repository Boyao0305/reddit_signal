import { formatCount } from '../../lib/seedcast/format'
import type { SeedcastCluster } from '../../lib/seedcast/types'
import TrendChart from './TrendChart'

interface Props {
  cluster: SeedcastCluster
  badge: string
  color: string
  badgeBg: string
  badgeInk: string
}

export default function MoverCard({ cluster: c, badge, color, badgeBg, badgeInk }: Props) {
  return (
    <a
      href={`#/cluster/${c.id}`}
      className="block rounded-[18px] bg-white px-6 pb-4 pt-6 text-seed-ink no-underline shadow-[0_10px_30px_rgba(58,22,112,0.12)] transition-shadow hover:shadow-[0_18px_44px_rgba(58,22,112,0.22)]"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-2xl font-bold tracking-[-0.01em]">{c.name}</span>
        <span className="whitespace-nowrap rounded-full px-3 py-1.5 font-seedmono text-sm font-medium" style={{ background: badgeBg, color: badgeInk }}>
          {badge}
        </span>
      </div>
      <div className="mt-3">
        <TrendChart values={c.series} color={color} width={460} height={250} ariaLabel={`${c.name}, weekly posts over 26 weeks`} />
      </div>
      <div className="mt-1.5 flex flex-col gap-2.5 border-t border-[#F0EAF8] pb-2.5 pt-3.5">
        <p className="m-0 text-[15px] leading-snug text-seed-ink2">{c.statement}</p>
        <div className="flex flex-wrap items-center gap-x-[18px] gap-y-2 font-seedmono text-[13px] text-seed-muted">
          <span className="rounded-full bg-seed-tint px-2.5 py-[3px] font-seed text-xs font-semibold text-seed-b2b">{c.audience}</span>
          <span><strong className="font-medium text-seed-ink">{formatCount(c.posts4w)}</strong> posts / 4 wks</span>
          <span><strong className="font-medium text-seed-ink">{formatCount(c.total)}</strong> total</span>
          <span><strong className="font-medium text-seed-ink">{c.communities}</strong> communities</span>
          <span>{c.topCommunity}</span>
        </div>
      </div>
    </a>
  )
}
