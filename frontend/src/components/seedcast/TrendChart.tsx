import { useId } from 'react'
import { areaPath, scaleSeries, toPath } from '../../lib/seedcast/paths'

interface Props {
  values: number[]
  color: string
  /** Last point is a partial period and is drawn dashed. */
  partialLast?: boolean
  width: number
  height: number
  ticks?: Array<{ at: number; label: string }>
  ariaLabel: string
  strokeWidth?: number
  dotRadius?: number
}

/** Area + line chart with dashed gridlines. Pure SVG, no chart library. */
export default function TrendChart({ values, color, partialLast = false, width, height, ticks = [], ariaLabel, strokeWidth = 4, dotRadius = 6 }: Props) {
  const gid = useId()
  const plotH = ticks.length ? height - 32 : height
  const opts = { top: 28, bottom: 24 }
  // Inset the plot so the end dot is not clipped by the viewBox.
  const inner = width - dotRadius - 4
  const pts = scaleSeries(values, inner, plotH, opts)
  const end = pts[pts.length - 1]
  const grid = [0.1, 0.3, 0.5, 0.7, 0.9].map((f) => Math.round(plotH * f))
  const solidPath = toPath(partialLast ? pts.slice(0, -1) : pts)
  const lastTwo = pts.slice(-2)
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="block h-auto w-full" role="img" aria-label={ariaLabel}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.24" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <g stroke="#E6DFF1" strokeDasharray="4 5">
        {grid.map((y) => (
          <line key={y} x1={0} y1={y} x2={width} y2={y} />
        ))}
      </g>
      <path d={areaPath(values, inner, plotH, opts)} fill={`url(#${gid})`} />
      <path d={solidPath} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
      {partialLast && lastTwo.length === 2 && (
        <path
          d={`M${lastTwo[0][0].toFixed(1)} ${lastTwo[0][1].toFixed(1)} L${lastTwo[1][0].toFixed(1)} ${lastTwo[1][1].toFixed(1)}`}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray="6 6"
          strokeLinecap="round"
        />
      )}
      {end && <circle cx={end[0]} cy={end[1]} r={dotRadius} fill={color} stroke="#fff" strokeWidth={2.5} />}
      {ticks.map((t) => (
        <text key={t.label + t.at} x={t.at * inner} y={height - 8} textAnchor="middle" fontSize={12} fill="#6A5F82" fontFamily="IBM Plex Mono, monospace">
          {t.label}
        </text>
      ))}
    </svg>
  )
}
