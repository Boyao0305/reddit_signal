export interface ScaleOptions {
  /** Pixels reserved above the highest value. */
  top?: number
  /** Pixels reserved below the lowest value (baseline sits at h - bottom). */
  bottom?: number
  /** Shared maximum, e.g. when several series use one axis. */
  max?: number
}

export type Point = [number, number]

export function scaleSeries(values: number[], w: number, h: number, opts: ScaleOptions = {}): Point[] {
  const top = opts.top ?? 4
  const bottom = opts.bottom ?? 4
  const max = opts.max ?? Math.max(...values, 1)
  const n = values.length
  return values.map((v, i) => [
    n > 1 ? (i * w) / (n - 1) : 0,
    h - bottom - (v / max) * (h - top - bottom),
  ])
}

export function toPath(points: Point[]): string {
  return points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
}

export function linePath(values: number[], w: number, h: number, opts?: ScaleOptions): string {
  return toPath(scaleSeries(values, w, h, opts))
}

/** Closed area under the line down to y = h. */
export function areaPath(values: number[], w: number, h: number, opts?: ScaleOptions): string {
  const pts = scaleSeries(values, w, h, opts)
  if (!pts.length) return ''
  return `${toPath(pts)} L${w.toFixed(1)} ${h} L0 ${h} Z`
}
