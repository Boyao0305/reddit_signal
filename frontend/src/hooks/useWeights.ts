import { useEffect, useState, useCallback, useMemo } from 'react'
import { useScoreConfig } from './useScoreConfig'
import type { DimensionGroup, DimensionKey, ScoreConfig, Weights } from '../types'

export interface UseWeightsResult {
  weights: Weights
  defaults: Weights
  config: ScoreConfig | null
  setWeight: (key: DimensionKey, value: number) => void
  resetAll: () => void
  resetGroup: (group: DimensionGroup) => void
}

const FALLBACK_DEFAULTS: Weights = {
  coherence: 1,
  thread_diversity: 1,
  author_diversity: 1,
  subreddit_count: 1,
  specificity_mean: 1,
  workaround_ratio: 1,
  separation: 1,
  temporal_spread_days: 1,
  q_specificity: 1,
  v_buildability: 1,
  v_icp_clarity: 1,
  v_moat_potential: 1,
  slope_per_week: 0,
  velocity_4w_ratio_normalized: 0,
}

export function useWeights(enabled = true): UseWeightsResult {
  const { data: config } = useScoreConfig(enabled)
  const defaults = useMemo<Weights>(() => {
    if (!config) return FALLBACK_DEFAULTS
    const out = {} as Weights
    for (const d of config.dimensions) out[d.key] = d.default_weight
    return out
  }, [config])

  const [weights, setWeights] = useState<Weights>(FALLBACK_DEFAULTS)

  // When config arrives, snap to its defaults (unless user already touched something).
  // For simplicity (and per §3.2 no-localStorage requirement), we just overwrite on
  // first config load. Subsequent config refetches don't clobber.
  const [hydrated, setHydrated] = useState(false)
  useEffect(() => {
    if (config && !hydrated) {
      const next = {} as Weights
      for (const d of config.dimensions) next[d.key] = d.default_weight
      setWeights(next)
      setHydrated(true)
    }
  }, [config, hydrated])

  const setWeight = useCallback((key: DimensionKey, value: number) => {
    setWeights((w) => ({ ...w, [key]: value }))
  }, [])

  const resetAll = useCallback(() => {
    setWeights({ ...defaults })
  }, [defaults])

  const resetGroup = useCallback(
    (group: DimensionGroup) => {
      if (!config) return
      setWeights((w) => {
        const next = { ...w }
        for (const d of config.dimensions) {
          if (d.group === group) next[d.key] = d.default_weight
        }
        return next
      })
    },
    [config],
  )

  return { weights, defaults, config, setWeight, resetAll, resetGroup }
}
