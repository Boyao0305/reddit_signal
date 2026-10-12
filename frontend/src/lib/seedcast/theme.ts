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

const DARK_FILLS = new Set<string>([SEED.accent, AUDIENCE_FILL.B2B, AUDIENCE_FILL.B2C])

/** Label color that stays readable on a given bubble fill. */
export function inkOn(fill: string): string {
  return DARK_FILLS.has(fill) ? '#FFFFFF' : SEED.ink
}

export const COMPARE_COLORS = ['#632CA6', '#F08A24', '#1E8E8E', '#C2255C'] as const
