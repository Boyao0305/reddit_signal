import { useState } from 'react'
import type { Member, MembersResponse } from '../types'

export interface MembersListProps {
  members: MembersResponse | null
  page: number
  onPageChange: (p: number) => void
}

function sentimentClass(score: number | null): string {
  if (score === null || score === undefined) return 'bg-stone-100 text-stone-500'
  if (score <= -0.3) return 'bg-rose-100 text-rose-700'
  if (score >= 0.3) return 'bg-emerald-100 text-emerald-700'
  return 'bg-stone-100 text-stone-600'
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function sourceLabel(m: Member): string {
  const source = (m.source ?? 'reddit').toLowerCase()
  const surface = m.source_surface ?? m.subreddit_name
  if (source === 'reddit') return surface ? `r/${surface}` : 'reddit'
  if (source === 'twitter') return surface ? `X/${surface}` : 'X'
  if (source === 'facebook') return surface ? `Facebook/${surface}` : 'Facebook'
  if (source === 'hackernews') return surface ? `HN/${surface}` : 'Hacker News'
  if (source === 'youtube') return surface ? `YouTube/${surface}` : 'YouTube'
  return surface ? `${source}/${surface}` : source
}

function MemberCard({ m }: { m: Member }) {
  const [expanded, setExpanded] = useState(false)
  const long = (m.raw_quote ?? '').length > 220
  return (
    <div className="bg-white rounded-2xl ring-1 ring-stone-200/60 shadow-[0_1px_3px_rgba(28,25,23,0.04)] px-4 py-3">
      <div className="flex items-center gap-2 text-[11px] text-stone-500">
        <span className="font-medium text-stone-600">
          {sourceLabel(m)}
        </span>
        <span>·</span>
        <span>{formatDate(m.source_created)}</span>
        {m.sentiment_score !== null && (
          <span
            className={`ml-auto px-1.5 py-0.5 rounded-full text-[10px] font-medium ${sentimentClass(m.sentiment_score)}`}
            title="sentiment_score"
          >
            {m.sentiment_score.toFixed(2)}
          </span>
        )}
        {m.is_workaround && (
          <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-100 text-amber-700">
            workaround
          </span>
        )}
      </div>
      <div className="mt-1.5 text-sm text-stone-900 leading-snug">
        {m.pain_statement ?? '—'}
      </div>
      {m.raw_quote && (
        <div className="mt-1.5 text-xs text-stone-500 italic leading-snug">
          {expanded || !long
            ? m.raw_quote
            : m.raw_quote.slice(0, 220).trimEnd() + '…'}
          {long && (
            <button
              type="button"
              onClick={() => setExpanded((e) => !e)}
              className="ml-1 text-stone-600 hover:text-stone-900 underline-offset-2 hover:underline not-italic"
            >
              {expanded ? 'show less' : 'show more'}
            </button>
          )}
        </div>
      )}
      {m.mentioned_tools && m.mentioned_tools.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {m.mentioned_tools.map((t) => (
            <span
              key={t}
              className="px-1.5 py-0.5 rounded-md bg-stone-100 text-stone-600 text-[10px]"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function MembersList({ members, page, onPageChange }: MembersListProps) {
  if (!members) {
    return <div className="text-sm text-stone-400">Loading members…</div>
  }
  const { pages, total } = members
  return (
    <div className="flex flex-col gap-2">
      {members.members.map((m) => (
        <MemberCard key={m.pain_id} m={m} />
      ))}
      <div className="flex items-center justify-between text-xs text-stone-500 mt-1">
        <div>
          Page {page} of {pages} · {total.toLocaleString()} quotes
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange(Math.max(1, page - 1))}
            className="px-2.5 py-1 rounded-md ring-1 ring-stone-200 bg-white hover:bg-stone-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            ← Prev
          </button>
          <button
            type="button"
            disabled={page >= pages}
            onClick={() => onPageChange(Math.min(pages, page + 1))}
            className="px-2.5 py-1 rounded-md ring-1 ring-stone-200 bg-white hover:bg-stone-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  )
}
