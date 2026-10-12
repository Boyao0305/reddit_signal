import { useMemo, useState } from 'react'
import { useSeedcastSnapshot } from '../../hooks/useSeedcast'
import { formatCount, formatRatio, momentumOf } from '../../lib/seedcast/format'
import { scaleSeries, toPath } from '../../lib/seedcast/paths'
import { COMPARE_COLORS, SEED } from '../../lib/seedcast/theme'
import type { SeedcastCluster } from '../../lib/seedcast/types'
import Pill from './Pill'
import SiteShell from './SiteShell'
import StateNotice from './StateNotice'

const MAX_SELECTED = 4
const W = 1000

function Metric({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <div>
      <div className="font-seedmono text-2xl font-medium" style={color ? { color } : undefined}>{value}</div>
      <div className="text-xs text-seed-muted">{label}</div>
    </div>
  )
}

export default function ComparePage() {
  const { data, error, loading } = useSeedcastSnapshot()
  const [selectedIds, setSelectedIds] = useState<number[]>([1, 21, 62])
  const [query, setQuery] = useState('')
  const all = data?.clusters ?? []

  const byId = useMemo(() => new Map(all.map((c) => [c.id, c])), [all])
  const chosen = selectedIds.map((id) => byId.get(id)).filter((c): c is SeedcastCluster => !!c)
  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    return all
      .filter((c) => !selectedIds.includes(c.id) && (!q || c.name.toLowerCase().includes(q)))
      .slice(0, 5)
  }, [all, query, selectedIds])

  return (
    <SiteShell active="compare" title="Compare" lift="sm" footerNote="Each line scaled to its own peak · Up to 4 clusters">
      {loading || error || !all.length ? (
        <StateNotice loading={loading} error={error} empty={!all.length} />
      ) : (
        <>
          <section className="rounded-[18px] border border-seed-line bg-white p-6 shadow-[0_16px_44px_rgba(58,22,112,0.16)]">
            <div className="flex flex-wrap items-start gap-5">
              <div className="flex min-w-0 max-w-[440px] flex-[1_1_320px] flex-col gap-1.5">
                <label htmlFor="seedcast-compare-search" className="sr-only">Search clusters</label>
                <input
                  id="seedcast-compare-search"
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={`Search ${all.length} clusters`}
                  className="min-h-[48px] rounded-full border-[1.5px] border-seed-line bg-seed-bg px-[18px] text-base text-seed-ink"
                />
                <div className="overflow-hidden rounded-[14px] border border-seed-line">
                  {results.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      disabled={selectedIds.length >= MAX_SELECTED}
                      onClick={() => setSelectedIds((ids) => (ids.length < MAX_SELECTED ? [...ids, c.id] : ids))}
                      className="flex min-h-[44px] w-full cursor-pointer items-center justify-between gap-3 border-0 border-b border-[#F0EAF8] bg-white px-4 text-left text-[15px] text-seed-ink disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span>{c.name}</span>
                      <span className="font-seedmono text-xs text-seed-muted">{c.posts4w} posts</span>
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex min-w-0 flex-[2_1_420px] flex-wrap gap-2.5">
                {chosen.map((c, i) => (
                  <button
                    key={c.id}
                    type="button"
                    aria-label={`Remove ${c.name}`}
                    onClick={() => setSelectedIds((ids) => ids.filter((x) => x !== c.id))}
                    className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-full border-2 bg-white px-4 text-[15px] font-semibold text-seed-ink"
                    style={{ borderColor: COMPARE_COLORS[i] }}
                  >
                    <span className="inline-block h-3 w-3 rounded-full" style={{ background: COMPARE_COLORS[i] }} />
                    {c.name}
                    <span aria-hidden="true" className="text-lg leading-none text-seed-muted">×</span>
                  </button>
                ))}
              </div>
            </div>
            <svg viewBox="0 0 1140 380" className="mt-5 block h-auto w-full" role="img" aria-label="Overlay of the selected clusters, each scaled to its own peak">
              <g stroke={SEED.line} strokeDasharray="4 5">
                {[30, 95, 160, 225, 290].map((y) => (
                  <line key={y} x1={0} y1={y} x2={W} y2={y} />
                ))}
              </g>
              {chosen.map((c, i) => {
                const pts = scaleSeries(c.series, W, 350, { top: 30, bottom: 30 })
                const end = pts[pts.length - 1]
                return (
                  <g key={c.id}>
                    <path d={toPath(pts)} fill="none" stroke={COMPARE_COLORS[i]} strokeWidth={4} strokeLinejoin="round" strokeLinecap="round" />
                    <circle cx={end[0]} cy={end[1]} r={6} fill={COMPARE_COLORS[i]} stroke="#fff" strokeWidth={2.5} />
                    <text x={end[0] + 16} y={end[1] + 5} fontSize={14} fontWeight={700} fill={COMPARE_COLORS[i]} fontFamily="DM Sans, sans-serif">{c.name}</text>
                  </g>
                )
              })}
            </svg>
          </section>

          <section className="mt-6 grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-5">
            {chosen.map((c, i) => (
              <a
                key={c.id}
                href={`#/cluster/${c.id}`}
                className="flex flex-col gap-4 rounded-[18px] border border-seed-line border-t-[6px] bg-white p-[22px] text-seed-ink no-underline"
                style={{ borderTopColor: COMPARE_COLORS[i] }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xl font-bold">{c.name}</span>
                  <Pill size="sm">{c.audience}</Pill>
                </div>
                <div className="grid grid-cols-2 gap-3.5">
                  <Metric value={formatCount(c.posts4w)} label="posts, 4 weeks" />
                  <Metric value={formatRatio(c.ratio)} label="vs prior 4" color={momentumOf(c.ratio) === 'fading' ? SEED.fadeInk : SEED.accent} />
                  <Metric value={formatCount(c.total)} label="posts, total" />
                  <Metric value={String(c.communities)} label="communities" />
                </div>
              </a>
            ))}
          </section>
        </>
      )}
    </SiteShell>
  )
}
