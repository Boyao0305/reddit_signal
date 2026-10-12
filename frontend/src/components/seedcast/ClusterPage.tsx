import { useState } from 'react'
import { useSeedcastCluster } from '../../hooks/useSeedcast'
import { formatCount, formatRatio, momentumOf } from '../../lib/seedcast/format'
import { SEED } from '../../lib/seedcast/theme'
import Pill from './Pill'
import SiteShell from './SiteShell'
import StateNotice from './StateNotice'
import TrendChart from './TrendChart'

const MONTHS = ['Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep']
const SOURCE_COLORS = ['#632CA6', '#F08A24', '#A678E0', '#3A1A6B']

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3.5 rounded-[18px] border border-seed-line bg-white p-7">
      <h2 className="m-0 text-[22px] font-bold">{title}</h2>
      {children}
    </div>
  )
}

export default function ClusterPage({ id }: { id: number }) {
  const { data: c, error, loading } = useSeedcastCluster(id)
  const [metric, setMetric] = useState<'posts' | 'share'>('posts')

  const back = (
    <a href="#/" className="mt-6 inline-block py-2.5 text-[15px] text-[#D9C8F5] no-underline">
      ← Landscape
    </a>
  )
  if (loading || error || !c) {
    return (
      <SiteShell active={null} title="Cluster" eyebrow={back} lift="lg">
        <StateNotice loading={loading} error={error} empty={!c} />
      </SiteShell>
    )
  }

  const values = metric === 'posts' ? c.history : c.share
  const n = values.length
  const ticks = [4, 13, 22, 30, 39, 48].filter((i) => i < n).map((i, k) => ({ at: i / (n - 1), label: MONTHS[(k * 2) % 12] }))
  const rising = momentumOf(c.ratio) === 'rising'
  const stats = [
    { value: formatCount(c.total), label: 'posts, all time' },
    { value: formatCount(c.posts4w), label: 'posts, last 4 weeks' },
    { value: formatRatio(c.ratio), label: 'vs prior 4 weeks', color: rising ? SEED.accent : undefined },
    { value: String(c.communities), label: 'communities' },
    { value: `${c.workaroundPct}%`, label: 'describe a workaround' },
  ]

  return (
    <SiteShell
      active={null}
      title={c.name}
      eyebrow={back}
      lift="lg"
      aside={
        <div className="flex gap-2 pb-2">
          <Pill>{c.audience}</Pill>
          {c.bursting && <Pill tone="burst">Bursting</Pill>}
        </div>
      }
      footerNote="Open source · No paywall"
    >
      <section className="rounded-[18px] border border-seed-line bg-white p-6 shadow-[0_16px_44px_rgba(58,22,112,0.16)]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div role="group" aria-label="Metric" className="flex gap-2">
            {(['posts', 'share'] as const).map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={metric === m}
                onClick={() => setMetric(m)}
                className={`min-h-[44px] cursor-pointer rounded-full border-[1.5px] px-5 text-[15px] font-semibold ${metric === m ? 'border-seed-accent bg-seed-accent text-white' : 'border-seed-line bg-transparent text-seed-ink'}`}
              >
                {m === 'posts' ? 'Posts' : 'Share of all'}
              </button>
            ))}
          </div>
          <span className="flex items-center gap-2 text-[13px] text-seed-muted">
            <svg width="28" height="6" aria-hidden="true"><line x1="0" y1="3" x2="28" y2="3" stroke={SEED.accent} strokeWidth="3" strokeDasharray="5 4" /></svg>
            This week so far
          </span>
        </div>
        <div className="mt-3">
          <TrendChart values={values} color={SEED.accent} partialLast width={1140} height={380} ticks={ticks} ariaLabel={`Weekly ${metric === 'posts' ? 'posts' : 'share of all posts'} for ${c.name} over the last year`} dotRadius={7} />
        </div>
        <div className="mt-3 grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-px overflow-hidden rounded-xl border border-seed-line bg-seed-line">
          {stats.map((s) => (
            <div key={s.label} className="bg-white px-[18px] py-4">
              <div className="font-seedmono text-[26px] font-medium" style={s.color ? { color: s.color } : undefined}>{s.value}</div>
              <div className="mt-0.5 text-[13px] text-seed-muted">{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-6 flex flex-wrap items-start gap-6">
        <div className="flex min-w-0 flex-[1_1_560px] flex-col gap-3.5 rounded-[18px] border border-seed-line bg-white p-7">
          <h2 className="m-0 text-[22px] font-bold">In their words</h2>
          {c.quotes.map((q, i) => (
            <figure key={i} className="m-0 rounded-xl bg-seed-bg p-[18px]">
              <blockquote className="m-0 text-[17px] leading-normal">{q.text}</blockquote>
              <figcaption className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2 text-[13px] text-seed-muted">
                <Pill size="sm">{q.source}</Pill>
                <span className="font-seedmono">{q.where}</span>
                <span className="font-seedmono">{q.date}</span>
                {q.url && (
                  <a href={q.url} rel="noopener noreferrer" className="ml-auto py-1.5 font-semibold text-seed-accent no-underline">
                    Original ↗
                  </a>
                )}
              </figcaption>
            </figure>
          ))}
        </div>
        <div className="flex min-w-0 flex-[1_1_380px] flex-col gap-6">
          <Card title="Where">
            {c.where.map((w) => (
              <div key={w.name} className="flex items-center gap-3 text-sm">
                <span className="w-[130px] shrink-0 truncate font-seedmono">{w.name}</span>
                <span className="h-3 flex-1 overflow-hidden rounded-md bg-seed-tint"><span className="block h-full rounded-md bg-seed-accent" style={{ width: `${w.pct}%` }} /></span>
                <span className="w-[42px] text-right font-seedmono text-seed-muted">{w.pct}%</span>
              </div>
            ))}
            <div className="mt-3 flex h-3.5 overflow-hidden rounded-[7px]" aria-hidden="true">
              {c.sources.map((s, i) => (
                <span key={s.name} className="block h-full" style={{ width: `${s.pct}%`, background: SOURCE_COLORS[i % SOURCE_COLORS.length] }} />
              ))}
            </div>
            <ul className="m-0 flex list-none flex-wrap gap-x-[18px] gap-y-2 p-0 text-[13px] text-seed-muted">
              {c.sources.map((s, i) => (
                <li key={s.name} className="flex items-center gap-1.5">
                  <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: SOURCE_COLORS[i % SOURCE_COLORS.length] }} />
                  {s.name} {s.pct}%
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Tools mentioned">
            <div className="flex flex-wrap gap-2">
              {c.tools.map((t) => (
                <span key={t.name} className="rounded-full bg-seed-tint px-3.5 py-2 text-[15px] font-medium text-seed-b2b">
                  {t.name} <span className="font-seedmono text-xs text-seed-muted">{t.n}</span>
                </span>
              ))}
            </div>
          </Card>
        </div>
      </section>
    </SiteShell>
  )
}
