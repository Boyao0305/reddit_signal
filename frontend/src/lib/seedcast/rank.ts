import type { MoverMode, SeedcastCluster } from './types'

export const MOVER_MIN_POSTS = 20

/** Rank clusters for the Movers page. Tiny clusters never qualify. */
export function rankMovers(
  clusters: SeedcastCluster[],
  mode: MoverMode,
  minPosts: number = MOVER_MIN_POSTS,
): SeedcastCluster[] {
  const pool = clusters.filter((c) => {
    if (c.posts4w < minPosts) return false
    return mode === 'burst' ? c.burst != null : c.ratio != null
  })
  const key = (c: SeedcastCluster): number => {
    if (mode === 'burst') return -(c.burst ?? 0)
    return mode === 'up' ? -(c.ratio ?? 0) : (c.ratio ?? 0)
  }
  return pool.slice().sort((a, b) => key(a) - key(b))
}
