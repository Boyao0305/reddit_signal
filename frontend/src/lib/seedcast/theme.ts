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

/** Keyword color on each bubble fill (Seedcast style rules). */
const WORD_ON: Record<string, string> = {
  [SEED.accent]: '#FFC46B', // rising (deep violet) -> warm amber
  [SEED.fade]: '#3A1670', // fading (orange) -> deep violet
  [SEED.steady]: '#632CA6', // flat / unclear (pale lilac) -> violet
  [AUDIENCE_FILL.B2B]: '#F6B96B', // B2B (dark violet) -> amber
  [AUDIENCE_FILL.B2C]: '#2A0F52', // B2C (light violet) -> very dark violet
  [AUDIENCE_FILL.Mixed]: '#3A1670', // mixed (amber) -> deep violet
}

/** Label color that stays readable on a given bubble fill. */
export function wordOn(fill: string): string {
  return WORD_ON[fill] ?? SEED.ink
}

export const COMPARE_COLORS = ['#632CA6', '#F08A24', '#1E8E8E', '#C2255C'] as const
