import { useEffect, useState } from 'react'
import { getSeedcastClusterDetail, getSeedcastSnapshot } from '../api/seedcast'
import type { SeedcastClusterDetail, SeedcastSnapshot } from '../lib/seedcast/types'

export interface Loadable<T> {
  data: T | null
  error: string | null
  loading: boolean
}

function useLoad<T>(load: () => Promise<T>, key: string): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ data: null, error: null, loading: true })
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: null }))
    load().then(
      (data) => alive && setState({ data, error: null, loading: false }),
      (err: unknown) => alive && setState({ data: null, error: err instanceof Error ? err.message : String(err), loading: false }),
    )
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return state
}

export function useSeedcastSnapshot(): Loadable<SeedcastSnapshot> {
  return useLoad(getSeedcastSnapshot, 'snapshot')
}

export function useSeedcastCluster(id: number): Loadable<SeedcastClusterDetail | null> {
  return useLoad(() => getSeedcastClusterDetail(id), `cluster-${id}`)
}
