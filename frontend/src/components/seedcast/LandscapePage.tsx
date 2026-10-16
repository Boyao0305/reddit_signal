import { useSeedcastSnapshot } from '../../hooks/useSeedcast'
import { formatDate, formatRatio, momentumOf } from '../../lib/seedcast/format'
import { SEED } from '../../lib/seedcast/theme'
import type { SeedcastCluster } from '../../lib/seedcast/types'
import BubbleLandscape from './BubbleLandscape'
import ClusterRowList from './ClusterRowList'
import SiteShell from './SiteShell'
import StateNotice from './StateNotice'

function ratioBadge(c: SeedcastCluster) {
  const m = momentumOf(c.ratio)
  return { text: formatRatio(c.ratio), color: m === 'rising' ? SEED.accent : m === 'fading' ? SEED.fadeInk : SEED.muted }
}

export default function LandscapePage() {
  const { data, error, loading } = useSeedcastSnapshot()
  const clusters = (data?.clusters ?? []).slice().sort((a, b) => b.posts4w - a.posts4w)
  return (
    <SiteShell
      active="landscape"
      title="What people can't stop complaining about"
      aside={<span className="pb-2 font-seedmono text-[13px] text-[#D9C8F5]">Updated {formatDate(data?.updatedAt ?? null)}</span>}
    >
      {loading || error || !clusters.length ? (
        <StateNotice loading={loading} error={error} empty={!clusters.length} />
      ) : (
        <>
          <BubbleLandscape clusters={clusters} />
          <ClusterRowList clusters={clusters} badge={ratioBadge} />
        </>
      )}
    </SiteShell>
  )
}
