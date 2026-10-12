import { twoLines } from './format'

export interface BubbleLabel {
  lines: string[]
  fontSize: number
}

const CHAR_W = 0.56 // average glyph width / font size for DM Sans bold
const MIN_FONT = 11
const MAX_FONT = 18

function fit(lines: string[], r: number): BubbleLabel | null {
  const longest = Math.max(...lines.map((l) => l.length))
  const byWidth = (r * 1.7) / (longest * CHAR_W)
  const byHeight = (r * 1.5) / (lines.length * 1.2)
  const size = Math.min(MAX_FONT, r * 0.32, byWidth, byHeight)
  return size >= MIN_FONT ? { lines, fontSize: Math.round(size * 10) / 10 } : null
}

/**
 * Theme words for a bubble. Large bubbles get the full name on up to two
 * lines; mid-size bubbles get just the theme keyword (first word); bubbles
 * too small for a readable label get none.
 */
export function bubbleLabel(name: string, r: number): BubbleLabel | null {
  const [a, b] = twoLines(name)
  const keyword = name.split(' ')[0]
  return fit(b ? [a, b] : [a], r) ?? fit([keyword], r)
}
