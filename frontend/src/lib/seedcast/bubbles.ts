import type { SeedcastCluster } from './types'

export interface BubbleOptions {
  /** Radius of the biggest cluster, whatever its absolute size. */
  maxRadius: number
  /** Clusters smaller than this radius are never drawn. */
  minRadius: number
  /** The biggest clusters, always shown (the labeled centre of the chart). */
  featured: number
  /** Medium tier: this many, spread evenly over ranks featured..mediumUntilRank. */
  medium: number
  mediumUntilRank: number
  /** Small tier: this many, spread evenly over the remaining ranks. */
  small: number
}

/** `k` items spread evenly over `items` (first and last included), in order. */
export function spread<T>(items: T[], k: number): T[] {
  if (k <= 0 || !items.length) return []
  if (k >= items.length) return items.slice()
  if (k === 1) return [items[0]]
  const picked = new Set<number>()
  for (let i = 0; i < k; i++) picked.add(Math.round((i * (items.length - 1)) / (k - 1)))
  return [...picked].sort((a, b) => a - b).map((i) => items[i])
}

/**
 * A sample of clusters across all sizes (not only the biggest), with radii at one fixed scale:
 * area is proportional to posts in the last 4 weeks and the biggest cluster always gets `maxRadius`.
 * The sample is deterministic, so the chart does not change between page loads.
 */
export function selectBubbles(clusters: SeedcastCluster[], opts: BubbleOptions): Array<{ cluster: SeedcastCluster; r: number }> {
  const sorted = clusters.filter((c) => c.posts4w > 0).sort((a, b) => b.posts4w - a.posts4w || a.id - b.id)
  if (!sorted.length) return []
  const top = sorted[0].posts4w
  const sized = sorted
    .map((cluster) => ({ cluster, r: opts.maxRadius * Math.sqrt(cluster.posts4w / top) }))
    .filter((b) => b.r >= opts.minRadius)
  return [
    ...sized.slice(0, opts.featured),
    ...spread(sized.slice(opts.featured, opts.mediumUntilRank), opts.medium),
    ...spread(sized.slice(opts.mediumUntilRank), opts.small),
  ]
}
