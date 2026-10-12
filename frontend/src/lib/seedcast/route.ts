export type SeedcastRoute =
  | { name: 'landscape' }
  | { name: 'movers' }
  | { name: 'compare' }
  | { name: 'cluster'; id: number }

/** Parse a location hash such as "#/cluster/12" (no router dependency). */
export function parseRoute(hash: string): SeedcastRoute {
  const path = hash.replace(/^#/, '').replace(/\/+$/, '') || '/'
  if (path === '/movers') return { name: 'movers' }
  if (path === '/compare') return { name: 'compare' }
  const m = /^\/cluster\/(\d+)$/.exec(path)
  if (m) return { name: 'cluster', id: Number(m[1]) }
  return { name: 'landscape' }
}
