import type { SeedcastCluster } from './types'

export interface BubbleOptions {
  /** Radius of the biggest cluster, whatever its absolute size. */
  maxRadius: number
  /** Clusters whose radius would be below this share of maxRadius are not drawn. */
  minRatio: number
  /** At most this many bubbles, biggest first. */
  maxCount: number
}

/** Which clusters get a bubble, and its radius: area proportional to posts in the last 4 weeks. */
export function selectBubbles(clusters: SeedcastCluster[], opts: BubbleOptions): Array<{ cluster: SeedcastCluster; r: number }> {
  const sorted = clusters.filter((c) => c.posts4w > 0).sort((a, b) => b.posts4w - a.posts4w)
  if (!sorted.length) return []
  const top = sorted[0].posts4w
  return sorted
    .map((cluster) => ({ cluster, r: opts.maxRadius * Math.sqrt(cluster.posts4w / top) }))
    .filter((b) => b.r >= opts.minRatio * opts.maxRadius)
    .slice(0, opts.maxCount)
}
