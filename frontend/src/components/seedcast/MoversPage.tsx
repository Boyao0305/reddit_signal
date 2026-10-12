import { useMemo, useState } from 'react'
import { useSeedcastSnapshot } from '../../hooks/useSeedcast'
import { formatBurst, formatRatio } from '../../lib/seedcast/format'
import { rankMovers } from '../../lib/seedcast/rank'
import { SEED } from '../../lib/seedcast/theme'
import type { MoverMode, SeedcastCluster } from '../../lib/seedcast/types'
import ClusterRowList from './ClusterRowList'
import MoverCard from './MoverCard'
import SiteShell from './SiteShell'
import StateNotice from './StateNotice'

const TABS: Array<{ mode: MoverMode; label: string }> = [
  { mode: 'up', label: 'Rising' },
  { mode: 'down', label: 'Fading' },
  { mode: 'burst', label: 'Bursting' },
]
const TOP = 10

export default function MoversPage() {
  const { data, error, loading } = useSeedcastSnapshot()
  const [mode, setMode] = useState<MoverMode>('up')
  const ranked = useMemo(() => rankMovers(data?.clusters ?? [], mode), [data, mode])
  const fading = mode === 'down'
  const color = fading ? SEED.fade : SEED.accent
  const text = (c: SeedcastCluster) => (mode === 'burst' ? formatBurst(c.burst) : formatRatio(c.ratio))

  const tabs = (
    <div role="group" aria-label="Ranking" className="flex flex-wrap gap-2">
      {TABS.map((t) => (
        <button
          key={t.mode}
          type="button"
          aria-pressed={mode === t.mode}
          onClick={() => setMode(t.mode)}
          className={`min-h-[44px] cursor-pointer rounded-full border-[1.5px] px-[22px] text-[15px] font-semibold ${mode === t.mode ? 'border-white bg-white text-seed-band' : 'border-white/40 bg-transparent text-white'}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  )

  return (
    <SiteShell active="movers" title="Top 10 movers" aside={tabs} lift="sm" footerNote="Last 26 weeks · Minimum 20 posts">
      {loading || error || !ranked.length ? (
        <StateNotice loading={loading} error={error} empty={!ranked.length} />
      ) : (
        <>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,480px),1fr))] gap-6">
            {ranked.slice(0, TOP).map((c) => (
              <MoverCard
                key={c.id}
                cluster={c}
                badge={text(c)}
                color={color}
                badgeBg={fading ? '#FDE7D0' : SEED.tint}
                badgeInk={fading ? '#8A3B00' : '#3A1A6B'}
              />
            ))}
          </div>
          <ClusterRowList
            clusters={ranked.slice(TOP)}
            startRank={TOP + 1}
            step={10}
            badge={(c) => ({ text: text(c), color: fading ? SEED.fadeInk : SEED.accent })}
          />
        </>
      )}
    </SiteShell>
  )
}
