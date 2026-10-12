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

/** "2026-10-05" -> "Oct 5, 2026"; returns the input when it does not parse. */
export function formatDate(iso: string | null): string {
  if (!iso) return '[date]'
  const d = new Date(`${iso}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}

/** Split a label into at most two balanced lines for bubble captions. */
export function twoLines(label: string): [string, string] {
  const words = label.split(' ')
  const mid = Math.ceil(words.length / 2)
  return [words.slice(0, mid).join(' '), words.slice(mid).join(' ')]
}
