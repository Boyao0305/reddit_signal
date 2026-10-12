// Composite-score engine (spec §6.4).
//
// Per-cluster score = Σ(weight_i · pct_i) / Σ(weight_i) over dimensions with
// w_i > 0 AND a non-null pct. missing_policy = "skip" (spec §6.4): if a
// dimension's pct is null, that term is dropped from both numerator and
// denominator (renormalisation). If no terms survive, score is null.

import type { Cluster, DimensionKey, Weights } from '../types'

export interface BreakdownRow {
  key: DimensionKey
  label: string
  weight: number
  pct: number | null
  contribution: number // weight * pct (raw, pre-normalisation)
  included: boolean
}

export function computeScore(cluster: Cluster, weights: Weights): number | null {
  let num = 0
  let den = 0
  ;(Object.keys(weights) as DimensionKey[]).forEach((k) => {
    const w = weights[k]
    if (!w || w <= 0) return
    const cell = cluster.dimensions?.[k]
    if (!cell || cell.pct === null || cell.pct === undefined) return
    num += w * cell.pct
    den += w
  })
  if (den <= 0) return null
  return num / den
}

/**
 * Detailed breakdown for the ScoreCell hover popover.
 * If `labels` is provided, uses friendly labels from score-config.
 * Skipped rows (null pct or w=0) are still returned with included=false so
 * the UI can grey them out.
 */
export function scoreBreakdown(
  cluster: Cluster,
  weights: Weights,
  labels?: Partial<Record<DimensionKey, string>>,
): BreakdownRow[] {
  return (Object.keys(weights) as DimensionKey[]).map((k) => {
    const w = weights[k] ?? 0
    const cell = cluster.dimensions?.[k]
    const pct = cell?.pct ?? null
    const included = w > 0 && pct !== null
    return {
      key: k,
      label: labels?.[k] ?? k,
      weight: w,
      pct,
      contribution: included && pct !== null ? w * pct : 0,
      included,
    }
  })
}
