import { useEffect, useState } from 'react'
import { fetchScoreConfig } from '../api'
import type { ScoreConfig } from '../types'

export interface UseScoreConfigResult {
  data: ScoreConfig | null
  loading: boolean
  error: string | null
}

export function useScoreConfig(enabled = true): UseScoreConfigResult {
  const [data, setData] = useState<ScoreConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) {
      setData(null)
      setError(null)
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    fetchScoreConfig()
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch((err) => {
        if (!cancelled) setError(String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [enabled])

  return { data, loading, error }
}
