import { sparklineStroke } from '../shared/colors'

export interface SparklineProps {
  data: Array<number | null>
  partialFromIndex: number
  width?: number
  height?: number
}

interface Point {
  x: number
  y: number
}

function buildSegments(
  data: Array<number | null>,
  startIdx: number,
  endIdx: number, // inclusive
  width: number,
  height: number,
  min: number,
  range: number,
): string[] {
  if (endIdx < startIdx) return []
  const n = data.length
  const xStep = n > 1 ? width / (n - 1) : 0
  const padY = 1
  const innerH = height - padY * 2
  const segments: string[] = []
  let current: Point[] = []
  for (let i = startIdx; i <= endIdx; i++) {
    const v = data[i]
    if (v === null || v === undefined) {
      if (current.length >= 2) {
        segments.push(current.map((p) => `${p.x},${p.y}`).join(' '))
      }
      current = []
      continue
    }
    const x = i * xStep
    const y = padY + (1 - (v - min) / range) * innerH
    current.push({ x, y })
  }
  if (current.length >= 2) {
    segments.push(current.map((p) => `${p.x},${p.y}`).join(' '))
  }
  return segments
}

export default function Sparkline({
  data,
  partialFromIndex,
  width = 80,
  height = 24,
}: SparklineProps) {
  const nonNull = data.filter((v): v is number => v !== null && v !== undefined)
  if (nonNull.length < 2) {
    return (
      <svg width={width} height={height} aria-hidden="true">
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="#d1d5db"
          strokeDasharray="2,2"
        />
      </svg>
    )
  }
  const min = Math.min(...nonNull)
  const max = Math.max(...nonNull)
  const range = max - min || 1

  const lastSolid = Math.min(partialFromIndex - 1, data.length - 1)
  const solidSegs =
    lastSolid >= 1 ? buildSegments(data, 0, lastSolid, width, height, min, range) : []
  // Dashed starts one index earlier than partialFromIndex so the boundary
  // segment (the "current" week edge) renders dashed. Matches §6.6 "last 1–2
  // segments dashed". Falls back to empty when partialFromIndex is at the start.
  const dashedStart = Math.max(0, partialFromIndex - 1)
  const dashedSegs =
    data.length - 1 > dashedStart
      ? buildSegments(data, dashedStart, data.length - 1, width, height, min, range)
      : []

  // Always render a faint dashed vertical marker at the partial-week boundary.
  // Anchored between the last completed week (partialFromIndex - 1) and the
  // partial week (partialFromIndex) so it stays visible even when boundaryX
  // would otherwise land on the SVG's right edge.
  // Guarantees §3.5 ("current week always visually distinguished") even when
  // the partial week's value is null (no aggregator row yet).
  const n = data.length
  const xStep = n > 1 ? width / (n - 1) : 0
  const boundaryX = Math.min(
    width - 1,
    Math.max(1, (partialFromIndex - 0.5) * xStep),
  )

  return (
    <svg width={width} height={height} aria-hidden="true">
      <line
        x1={boundaryX}
        x2={boundaryX}
        y1={0}
        y2={height}
        stroke="#9ca3af"
        strokeWidth={1}
        strokeDasharray="2,2"
        opacity={0.7}
      />
      {solidSegs.map((pts, i) => (
        <polyline
          key={`s${i}`}
          points={pts}
          fill="none"
          stroke={sparklineStroke}
          strokeWidth={1.25}
        />
      ))}
      {dashedSegs.map((pts, i) => (
        <polyline
          key={`d${i}`}
          points={pts}
          fill="none"
          stroke={sparklineStroke}
          strokeWidth={1.25}
          strokeDasharray="3,2"
          opacity={0.6}
        />
      ))}
    </svg>
  )
}
