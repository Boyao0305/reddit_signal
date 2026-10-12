// Audience badge palette + trend chip palette (Tailwind class names).
// Warm cream-themed palette per chunk-2 visual refresh.

export function audienceBadgeClasses(audience: string | null | undefined): string {
  switch ((audience ?? '').toLowerCase()) {
    case 'b2c':
      return 'bg-sky-100 text-sky-700 ring-1 ring-sky-200/70'
    case 'b2b':
      return 'bg-violet-100 text-violet-700 ring-1 ring-violet-200/70'
    case 'mixed':
      return 'bg-amber-100 text-amber-800 ring-1 ring-amber-200/70'
    case 'unclear':
    default:
      return 'bg-stone-200 text-stone-600 ring-1 ring-stone-300/60'
  }
}

export type TrendDirection = 'up' | 'down' | 'flat'

export function trendChipClasses(dir: TrendDirection): string {
  switch (dir) {
    case 'up':
      return 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200/70'
    case 'down':
      return 'bg-orange-100 text-orange-700 ring-1 ring-orange-200/70'
    case 'flat':
    default:
      return 'bg-stone-200 text-stone-600 ring-1 ring-stone-300/60'
  }
}

// Indigo reads warmer than teal against the cream background.
export const sparklineStroke = '#57534e' // stone-600 — warm dark gray, sits calmly on cream
