import { trendChipClasses, type TrendDirection } from '../shared/colors'

export interface TrendChipProps {
  value: number | null | undefined
  kind: 'ratio' | 'slope'
}

function classify(value: number | null | undefined, kind: 'ratio' | 'slope'): TrendDirection {
  if (value === null || value === undefined || Number.isNaN(value)) return 'flat'
  if (kind === 'ratio') {
    if (value >= 1.1) return 'up'
    if (value <= 0.9) return 'down'
    return 'flat'
  }
  // slope
  if (value > 2) return 'up'
  if (value < -2) return 'down'
  return 'flat'
}

function arrow(dir: TrendDirection): string {
  switch (dir) {
    case 'up':
      return '↗'
    case 'down':
      return '↘'
    default:
      return '→'
  }
}

function formatValue(value: number | null | undefined, kind: 'ratio' | 'slope'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return kind === 'ratio' ? value.toFixed(2) : value.toFixed(1)
}

export default function TrendChip({ value, kind }: TrendChipProps) {
  const dir = classify(value, kind)
  const text = formatValue(value, kind)
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium tabular-nums ${trendChipClasses(
        dir,
      )}`}
    >
      <span aria-hidden="true">{arrow(dir)}</span>
      <span>{text}</span>
    </span>
  )
}
