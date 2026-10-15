export interface PackedCircle {
  x: number
  y: number
  r: number
}

export interface PackOptions {
  width: number
  height: number
  /** Vertical squash of the search spiral so wide canvases fill sideways. */
  squash?: number
  gap?: number
  padding?: number
}

/**
 * Greedy spiral circle packing. `radii` should be sorted largest first.
 * Returns circles in input order, uniformly scaled to fit width x height.
 */
export function packCircles(radii: number[], opts: PackOptions): PackedCircle[] {
  const squash = opts.squash ?? 0.58
  const gap = opts.gap ?? 3
  const pad = opts.padding ?? 8
  const placed: PackedCircle[] = []

  radii.forEach((r, idx) => {
    if (idx === 0) {
      placed.push({ x: 0, y: 0, r })
      return
    }
    let ang = 0
    let rad = 0
    let spot: PackedCircle | null = null
    for (let guard = 0; guard < 20000 && !spot; guard++) {
      const x = Math.cos(ang) * rad
      const y = Math.sin(ang) * rad * squash
      const clear = placed.every((p) => {
        const dx = p.x - x
        const dy = p.y - y
        const m = p.r + r + gap
        return dx * dx + dy * dy >= m * m
      })
      if (clear) spot = { x, y, r }
      else {
        ang += 0.4
        rad += 0.45
      }
    }
    placed.push(spot ?? { x: rad, y: 0, r })
  })

  if (!placed.length) return []
  const x0 = Math.min(...placed.map((p) => p.x - p.r))
  const x1 = Math.max(...placed.map((p) => p.x + p.r))
  const y0 = Math.min(...placed.map((p) => p.y - p.r))
  const y1 = Math.max(...placed.map((p) => p.y + p.r))
  const k = Math.min((opts.width - 2 * pad) / (x1 - x0), (opts.height - 2 * pad) / (y1 - y0))
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  return placed.map((p) => ({
    x: opts.width / 2 + (p.x - cx) * k,
    y: opts.height / 2 + (p.y - cy) * k,
    r: p.r * k,
  }))
}

/**
 * Greedy spiral packing at a fixed scale: radii are used as given (no rescaling), and a circle that
 * finds no free spot inside the canvas is left out (null). `radii` should be sorted largest first.
 */
export function packFixed(radii: number[], opts: PackOptions): Array<PackedCircle | null> {
  const squash = opts.squash ?? 0.58
  const gap = opts.gap ?? 3
  const pad = opts.padding ?? 8
  const cx = opts.width / 2
  const cy = opts.height / 2
  const maxRad = Math.hypot(opts.width, opts.height) / 2
  const placed: PackedCircle[] = []
  const fits = (x: number, y: number, r: number) =>
    x - r >= pad && x + r <= opts.width - pad && y - r >= pad && y + r <= opts.height - pad &&
    placed.every((p) => {
      const m = p.r + r + gap
      return (p.x - x) ** 2 + (p.y - y) ** 2 >= m * m
    })

  return radii.map((r) => {
    for (let ang = 0, rad = 0; rad <= maxRad; ang += 0.4, rad += 0.45) {
      const x = cx + Math.cos(ang) * rad
      const y = cy + Math.sin(ang) * rad * squash
      if (fits(x, y, r)) {
        const spot = { x, y, r }
        placed.push(spot)
        return spot
      }
    }
    return null
  })
}
