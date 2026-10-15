export type Momentum = 'rising' | 'fading' | 'flat'

export const RISING_AT = 1.25
export const FADING_AT = 0.8

export function momentumOf(ratio: number | null): Momentum {
  if (ratio == null) return 'flat'
  if (ratio >= RISING_AT) return 'rising'
  if (ratio <= FADING_AT) return 'fading'
  return 'flat'
}

export function formatRatio(ratio: number | null): string {
  if (ratio == null) return '—'
  return `${ratio >= 1 ? '▲' : '▼'} ${ratio.toFixed(2)}×`
}

export function formatBurst(burst: number | null): string {
  return burst == null ? '—' : `${burst.toFixed(1)}× burst`
}

export function formatCount(n: number): string {
  return n.toLocaleString('en-US')
}

/** "2026-10-05" or a full ISO timestamp -> "Oct 5, 2026"; returns the input when it does not parse. */
export function formatDate(iso: string | null): string {
  if (!iso) return '[date]'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}

/** Split a label into at most two balanced lines for bubble captions. */
export function twoLines(label: string): [string, string] {
  const words = label.split(' ')
  const mid = Math.ceil(words.length / 2)
  return [words.slice(0, mid).join(' '), words.slice(mid).join(' ')]
}

const MAX_KEYWORD_CHARS = 28

/** Sentence case that keeps acronyms and brand casing: "AI Tools" -> "AI tools", "YouTube Experience" -> "YouTube experience". */
function sentenceCase(text: string): string {
  return text
    .split(' ')
    .map((w, i) => (i === 0 || /[A-Z].*[A-Z]|\d/.test(w) ? w : w.toLowerCase()))
    .join(' ')
}

/**
 * Short keyword for a bubble, taken from the topic that starts every cluster name:
 * "[AI Assistant]: Unreliable…" / "[Productivity: Information overload…]" / "Self-improvement: Users…".
 */
export function keywordOf(name: string): string {
  if (name.includes(' | ')) return '' // unlabeled cluster: its name is member statements joined, not a topic
  const topic = name.replace(/^\s*\[/, '').split(/[:\]]/)[0].trim()
  const words = topic.split(/\s+/).filter(Boolean)
  const short = topic.length > MAX_KEYWORD_CHARS ? words.slice(0, 3).join(' ') : words.join(' ')
  return sentenceCase(short)
}

export interface BubbleLabel {
  lines: string[]
  fontSize: number
  /** Baseline offsets from the circle centre, one per line. */
  dy: number[]
  /** Optional smaller line under the keyword (e.g. the post count). */
  sub?: { text: string; fontSize: number; dy: number }
}

const CHAR_WIDTH = 0.58 // average DM Sans bold glyph width, in em
const MIN_FONT = 9
const MAX_FONT = 22
const SUB_SCALE = 0.82

/**
 * Keyword layout inside a circle of radius r: font grows with the bubble; null when it cannot fit.
 * With `sub`, a smaller second line is added when there is room for it.
 */
export function bubbleLabel(keyword: string, r: number, sub?: string): BubbleLabel | null {
  if (!keyword) return null
  const height = (n: number, size: number, withSub: boolean) => (n + (withSub ? SUB_SCALE : 0)) * 1.15 * size
  const fits = (lines: string[], size: number, withSub: boolean) =>
    Math.max(...lines.map((l) => l.length), withSub && sub ? sub.length * SUB_SCALE : 0) * CHAR_WIDTH * size <= 1.6 * r &&
    height(lines.length, size, withSub) <= 1.3 * r
  const slash = keyword.indexOf('/')
  const options = keyword.includes(' ')
    ? [[keyword], twoLines(keyword)]
    : slash > 0
      ? [[keyword], [keyword.slice(0, slash + 1), keyword.slice(slash + 1)]] // "Parenting/" + "Sleep"
      : [[keyword]]
  for (const withSub of sub ? [true, false] : [false]) {
    for (let size = Math.min(MAX_FONT, Math.round(6 + r * 0.14)); size >= MIN_FONT; size -= 1) {
      const lines = options.find((o) => fits(o, size, withSub))
      if (!lines) continue
      const lh = 1.15 * size
      const top = -(height(lines.length, size, withSub) - lh) / 2 + 0.35 * size
      const round = (v: number) => Math.round(v * 10) / 10
      const label: BubbleLabel = { lines, fontSize: size, dy: lines.map((_, i) => round(top + i * lh)) }
      if (withSub && sub) {
        const subSize = Math.max(MIN_FONT - 1, Math.round(size * SUB_SCALE))
        label.sub = { text: sub, fontSize: subSize, dy: round(top + (lines.length - 1) * lh + 1.1 * subSize + 0.15 * size) }
      }
      return label
    }
  }
  return null
}
