import type { Audience } from './types'

export const SEED = {
  band: '#3A1670',
  bg: '#F5F2FA',
  ink: '#1D1433',
  ink2: '#4A3F66',
  muted: '#6A5F82',
  line: '#E6DFF1',
  accent: '#632CA6',
  tint: '#EFE8FA',
  fade: '#F08A24',
  fadeInk: '#B85C0A',
  steady: '#DDD5EA',
} as const

export const AUDIENCE_FILL: Record<Audience, string> = {
  B2B: '#3A1A6B',
  B2C: '#A678E0',
  Mixed: '#F6B96B',
  Unclear: SEED.steady,
}

// Theme-word colors per bubble fill: warm amber on the deep violets, deep
// violet on the oranges and pale tints. Every pair is at least 4.5:1.
const LABEL_INK: Record<string, string> = {
  [SEED.accent]: '#FFC46B',
  [AUDIENCE_FILL.B2B]: '#F6B96B',
  [AUDIENCE_FILL.B2C]: '#2A0F52',
  [AUDIENCE_FILL.Mixed]: '#3A1670',
  [SEED.fade]: '#3A1670',
  [SEED.steady]: '#632CA6',
}

/** Label color that stays readable on a given bubble fill. */
export function inkOn(fill: string): string {
  return LABEL_INK[fill] ?? SEED.ink
}

export const COMPARE_COLORS = ['#632CA6', '#F08A24', '#1E8E8E', '#C2255C'] as const
