import { getCachedJSON } from './client'
import { buildFixtureClusters, buildFixtureDetail, buildFixtureSnapshot } from '../lib/seedcast/fixtures'
import type { SeedcastClusterDetail, SeedcastSnapshot } from '../lib/seedcast/types'

// Calls the public read-only endpoints (/api/public/*). Set VITE_SEEDCAST_FIXTURES=1 to develop the
// UI against built-in sample data instead, without a backend.
const USE_FIXTURES: boolean = (import.meta.env.VITE_SEEDCAST_FIXTURES as string | undefined) === '1'

export async function getSeedcastSnapshot(): Promise<SeedcastSnapshot> {
  if (USE_FIXTURES) return buildFixtureSnapshot()
  return getCachedJSON<SeedcastSnapshot>('/api/public/snapshot', 300_000)
}

export async function getSeedcastClusterDetail(id: number): Promise<SeedcastClusterDetail | null> {
  if (USE_FIXTURES) {
    const c = buildFixtureClusters().find((x) => x.id === id)
    return c ? buildFixtureDetail(c) : null
  }
  return getCachedJSON<SeedcastClusterDetail>(`/api/public/clusters/${id}`, 300_000)
}
