import { linePath } from '../../lib/seedcast/paths'

interface Props {
  values: number[]
  color: string
  width?: number
  height?: number
}

export default function Sparkline({ values, color, width = 140, height = 36 }: Props) {
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} aria-hidden="true" className="shrink-0">
      <path
        d={linePath(values, width, height, { top: 4, bottom: 4 })}
        fill="none"
        stroke={color}
        strokeWidth={2.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}
