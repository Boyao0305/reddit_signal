import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { createElement } from 'react'
import { computeScore, scoreBreakdown } from './score'
import {
  computeExploreScore,
  DEFAULT_AXIS_WEIGHTS,
  type Cluster,
  type ClusterCompareSummary,
  type DimensionKey,
  type DimensionsMap,
  type MetricDelta,
  type Weights,
} from '../types'
import ClusterTable from '../components/ClusterTable'
import ClusterRow from '../components/ClusterRow'

const ALL_KEYS: DimensionKey[] = [
  'coherence',
  'thread_diversity',
  'author_diversity',
  'subreddit_count',
  'specificity_mean',
  'workaround_ratio',
  'separation',
  'temporal_spread_days',
  'q_specificity',
  'v_buildability',
  'v_icp_clarity',
  'v_moat_potential',
  'slope_per_week',
  'velocity_4w_ratio_normalized',
]

function makeDims(overrides: Partial<Record<DimensionKey, number | null>> = {}): DimensionsMap {
  const dims = {} as DimensionsMap
  for (const k of ALL_KEYS) {
    const pct = k in overrides ? overrides[k]! : 0.5
    dims[k] = { value: pct, pct }
  }
  return dims
}

function makeCluster(dims: DimensionsMap, patch: Partial<Cluster> = {}): Cluster {
  return {
    cluster_id: 1,
    label: 't',
    audience_type: 'B2C',
    audience_confidence: null,
    icp_hint: null,
    member_count: 10,
    pain_count_last_4w: 1,
    v_rationale: null,
    q_noise_flag: false,
    pareto_layer: null,
    balance_score: null,
    q_axis: null,
    m_axis: null,
    s_axis: null,
    is_eligible: null,
    low_confidence_s: null,
    competition_run_id: null,
    competition_status: null,
    market_structure: null,
    competition_confidence: null,
    external_supply_gap: null,
    competition_adjusted_s_axis: null,
    competition_adjusted_balance_score: null,
    competition_adjusted_pareto_layer: null,
    s_axis_source: 'missing',
    dimensions: dims,
    sparkline: [],
    sparkline_weeks: [],
    sparkline_partial_from_index: 0,
    ...patch,
  }
}

function unitWeights(value = 1): Weights {
  const w = {} as Weights
  for (const k of ALL_KEYS) w[k] = value
  return w
}

describe('computeScore', () => {
  it('case 1: equal weights, no nulls → arithmetic mean', () => {
    const pcts = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.15, 0.25, 0.35, 0.45]
    const dims = {} as DimensionsMap
    ALL_KEYS.forEach((k, i) => (dims[k] = { value: pcts[i], pct: pcts[i] }))
    const score = computeScore(makeCluster(dims), unitWeights(1))
    const mean = pcts.reduce((a, b) => a + b, 0) / pcts.length
    expect(score).toBeCloseTo(mean, 10)
  })

  it('case 2: one dim null → excluded; output in [0,1]', () => {
    const dims = makeDims({ coherence: null })
    const score = computeScore(makeCluster(dims), unitWeights(1))
    expect(score).not.toBeNull()
    expect(score!).toBeGreaterThanOrEqual(0)
    expect(score!).toBeLessThanOrEqual(1)
    // 13 surviving dims of value 0.5 → 0.5
    expect(score).toBeCloseTo(0.5, 10)
  })

  it('case 3: all weights = 0 → null', () => {
    expect(computeScore(makeCluster(makeDims()), unitWeights(0))).toBeNull()
  })

  it('case 4: empty dimensions map → null', () => {
    const c = makeCluster({} as DimensionsMap)
    expect(computeScore(c, unitWeights(1))).toBeNull()
  })

  it('case 5: cluster missing 2 of 3 velocity dims → still scoreable', () => {
    const dims = makeDims({
      slope_per_week: null,
      velocity_4w_ratio_normalized: null,
    })
    const w = unitWeights(1)
    const score = computeScore(makeCluster(dims), w)
    expect(score).not.toBeNull()
    expect(score!).toBeCloseTo(0.5, 10)
  })

  it('invariant: sum(breakdown.contribution) / totalWeight = computeScore', () => {
    const dims = makeDims({ coherence: null, separation: 0.9, q_specificity: 0.1 })
    const w = unitWeights(0)
    w.coherence = 0.5
    w.separation = 1.0
    w.q_specificity = 0.7
    w.v_buildability = 0.3
    const rows = scoreBreakdown(makeCluster(dims), w)
    const totalW = rows.filter((r) => r.included).reduce((a, r) => a + r.weight, 0)
    const sumC = rows.reduce((a, r) => a + r.contribution, 0)
    const score = computeScore(makeCluster(dims), w)
    expect(score).not.toBeNull()
    expect(sumC / totalW).toBeCloseTo(score!, 10)
  })
})

// ---- spec §5.3 — competition-adjusted Explore score ----------------------

describe('computeExploreScore', () => {
  it('case 1: equal axes with competition S, equal weights → that value', () => {
    expect(
      computeExploreScore(
        { q_axis: 0.5, m_axis: 0.5, s_axis: 0.1, competition_adjusted_s_axis: 0.5 },
        { q: 1, m: 1, s: 1 },
      ),
    ).toBe(0.5)
  })

  it('case 2: missing competition S with positive S weight → null', () => {
    expect(
      computeExploreScore({ q_axis: 0.5, m_axis: 0.5, s_axis: 0.9 }, { q: 1, m: 1, s: 1 }),
    ).toBeNull()
  })

  it('case 3: all weights zero → null', () => {
    expect(
      computeExploreScore({ q_axis: 0.5, m_axis: 0.5, s_axis: 0.5 }, { q: 0, m: 0, s: 0 }),
    ).toBeNull()
  })

  it('case 4: missing competition S is allowed when S weight is zero', () => {
    const v = computeExploreScore(
      { q_axis: 1, m_axis: 0.5, s_axis: 0.9 },
      { q: 1, m: 1, s: 0 },
    )
    expect(v).not.toBeNull()
    expect(v!).toBeCloseTo(0.75, 6)
  })

  it('uses competition-adjusted S when available', () => {
    const v = computeExploreScore(
      { q_axis: 0.6, m_axis: 0.6, s_axis: 0.1, competition_adjusted_s_axis: 0.9 },
      { q: 1, m: 1, s: 1 },
    )
    expect(v).toBeCloseTo(0.7, 6)
  })
})

// ---- spec §5.3 case 5 — default sort key --------------------------------

describe('ClusterTable default sort', () => {
  it('initial state sorts by pareto_layer ascending', () => {
    const { container } = render(
      createElement(ClusterTable, {
        clusters: [] as Cluster[],
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
      }),
    )
    const headers = Array.from(container.querySelectorAll('button'))
    const layer = headers.find((b) => (b.textContent ?? '').startsWith('Layer'))
    expect(layer).toBeTruthy()
    // The active column shows an ascending arrow; only the active column does.
    expect(layer!.textContent).toContain('▲')
    const withArrow = headers.filter((b) => /[▲▼]/.test(b.textContent ?? ''))
    expect(withArrow).toHaveLength(1)
    expect((withArrow[0].textContent ?? '').startsWith('Layer')).toBe(true)
  })

  it('can initialise with burst score descending', () => {
    const { container } = render(
      createElement(ClusterTable, {
        clusters: [] as Cluster[],
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        initialSortKey: 'burst_score',
        initialSortDir: 'desc',
      }),
    )
    const headers = Array.from(container.querySelectorAll('button'))
    const withArrow = headers.filter((b) => /[▲▼]/.test(b.textContent ?? ''))
    expect(withArrow).toHaveLength(1)
    expect((withArrow[0].textContent ?? '').startsWith('Burst')).toBe(true)
    expect(withArrow[0].textContent).toContain('▼')
  })
})

// ---- v2_034 burst sentinel — Emerging label + corpus banner ----------------

describe('ClusterTable burst sentinel', () => {
  it('renders the Emerging burst badge as "New"', () => {
    const c = makeCluster(makeDims(), {
      cluster_id: 1,
      label: 'Mid-month new topic',
      burst: {
        score: 0.4,
        lift: null,
        zscore: 3,
        recent_count: 7,
        baseline_count: 0,
        corpus_lift: 1.1,
        label: 'Emerging',
        window: '3d_vs_prior_28d',
      },
    })
    // Render ClusterRow directly — ClusterTable uses a virtualizer whose
    // viewport collapses in jsdom, so the row is not mounted there.
    const { container } = render(
      createElement(ClusterRow, {
        cluster: c,
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
      }),
    )
    // The Emerging badge renders the short label "New".
    expect(container.textContent).toContain('New')
  })

  it('shows the corpus burst banner when orphan_label is set', () => {
    const { container } = render(
      createElement(ClusterTable, {
        clusters: [] as Cluster[],
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        corpusBurst: {
          cluster_run_id: 10,
          day_anchor: '2026-06-23',
          total_recent: 120,
          total_baseline: 900,
          orphan_recent: 40,
          orphan_baseline: 50,
          orphan_lift: 7.4,
          orphan_zscore: 3.1,
          orphan_label: 'Bursting',
          corpus_lift: 1.05,
          snapshot_at: '2026-06-23T05:00:00+00:00',
        },
      }),
    )
    const text = container.textContent ?? ''
    expect(text).toContain('未聚类痛点激增')
    expect(text).toContain('40')
  })

  it('hides the corpus burst banner when orphan_label is null', () => {
    const { container } = render(
      createElement(ClusterTable, {
        clusters: [] as Cluster[],
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        corpusBurst: {
          cluster_run_id: 10,
          day_anchor: '2026-06-23',
          total_recent: 120,
          total_baseline: 900,
          orphan_recent: 2,
          orphan_baseline: 50,
          orphan_lift: 0.4,
          orphan_zscore: 0.1,
          orphan_label: null,
          corpus_lift: 1.05,
          snapshot_at: '2026-06-23T05:00:00+00:00',
        },
      }),
    )
    expect(container.textContent ?? '').not.toContain('未聚类痛点激增')
  })

  it('shows deterministic emerging opportunities above the cluster list', () => {
    const { container } = render(
      createElement(ClusterTable, {
        clusters: [] as Cluster[],
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        emergingOpportunities: [
          {
            id: 1,
            emerging_run_id: 9,
            cluster_run_id: 10,
            label: 'Users need faster refund dispute tracking',
            pain_count: 12,
            author_count: 5,
            subreddit_count: 3,
            coherence: 0.82,
            burst_score: 0.7,
            source_window_start: '2026-06-17',
            source_window_end: '2026-06-23',
            sample_pain_ids: [101],
            sample_quotes: [
              {
                pain_id: 101,
                pain_statement: 'Refund dispute tracking is too slow.',
                raw_quote: 'I cannot see where my refund dispute stands.',
                subreddit_name: 'shopping',
                source_author: 'u1',
                source_created: '2026-06-23T00:00:00Z',
              },
            ],
            created_at: '2026-06-23T05:00:00Z',
            lookback_days: 7,
          },
        ],
      }),
    )
    const text = container.textContent ?? ''
    expect(text).toContain('Emerging opportunities')
    expect(text).toContain('Users need faster refund dispute tracking')
    expect(text).toContain('12 pains')
  })
})

// ── Cluster cross-run compare ────────────────────────────────────────────────

function makeCompareSummary(overrides: Partial<ClusterCompareSummary> = {}): ClusterCompareSummary {
  const flatDelta: MetricDelta = {
    ref_value: 100,
    cmp_value: 100,
    absolute_change: 0,
    percent_change: 0,
    direction: 'flat',
  }
  return {
    cluster_id: 1,
    matched: true,
    member_count_delta: { ...flatDelta, ref_value: 100, cmp_value: 120, absolute_change: 20, percent_change: 0.2, direction: 'up' },
    pareto_layer_delta: { ...flatDelta, ref_value: 2, cmp_value: 1, absolute_change: -1, percent_change: -0.5, direction: 'down' },
    balance_score_delta: { ...flatDelta, ref_value: 0.6, cmp_value: 0.6, absolute_change: 0, percent_change: 0, direction: 'flat' },
    s_axis_delta: { ...flatDelta, ref_value: 0.8, cmp_value: 0.7, absolute_change: -0.1, percent_change: -0.125, direction: 'down' },
    ...overrides,
  }
}

describe('ClusterRow compare mode', () => {
  it('renders delta badge for member count change when compareSummary provided', () => {
    const dims = makeDims()
    const c = makeCluster(dims, { cluster_id: 1, member_count: 120 })
    const summary = makeCompareSummary()
    const { container } = render(
      createElement(ClusterRow, {
        cluster: c,
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        compareSummary: summary,
      }),
    )
    // The delta badge should show the up arrow and the change
    expect(container.textContent).toContain('120')
    expect(container.textContent).toContain('↑')
    expect(container.textContent).toContain('20')
  })

  it('renders delta badge for pareto layer down-shift', () => {
    const dims = makeDims()
    const c = makeCluster(dims, {
      cluster_id: 1,
      competition_adjusted_pareto_layer: 1,
    })
    const summary = makeCompareSummary({
      pareto_layer_delta: {
        ref_value: 2,
        cmp_value: 1,
        absolute_change: -1,
        percent_change: -0.5,
        direction: 'down',
      },
    })
    const { container } = render(
      createElement(ClusterRow, {
        cluster: c,
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        compareSummary: summary,
      }),
    )
    expect(container.textContent).toContain('L1')
    expect(container.textContent).toContain('↓')
  })

  it('does not render delta badges when compareSummary is null', () => {
    const dims = makeDims()
    const c = makeCluster(dims, { cluster_id: 1 })
    const { container } = render(
      createElement(ClusterRow, {
        cluster: c,
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        compareSummary: null,
      }),
    )
    // No arrow symbols should appear
    expect(container.textContent).not.toContain('↑')
    expect(container.textContent).not.toContain('↓')
  })

  it('shows unmatched state for cluster without lineage match', () => {
    const dims = makeDims()
    const c = makeCluster(dims, { cluster_id: 1 })
    const summary = makeCompareSummary({ matched: false })
    const { container } = render(
      createElement(ClusterRow, {
        cluster: c,
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        compareSummary: summary,
      }),
    )
    // Should render without delta indicators (no arrows for unmatched)
    expect(container.textContent).toContain('10') // member_count
    // No delta arrows since matched=false
    const arrows = container.querySelectorAll('[class*="text-emerald"], [class*="text-rose"]')
    expect(arrows.length).toBe(0)
  })
})

describe('ClusterTable compare mode', () => {
  it('accepts compareSummaries prop without crashing (virtualizer collapses in jsdom)', () => {
    const dims = makeDims()
    const c = makeCluster(dims, { cluster_id: 1, member_count: 150 })
    const summary = makeCompareSummary({ cluster_id: 1 })
    const { container } = render(
      createElement(ClusterTable, {
        clusters: [c],
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        compareSummaries: { 1: summary },
      }),
    )
    // Headers render, row content doesn't (virtualizer viewport collapses in jsdom)
    expect(container.textContent).toContain('Members')
    expect(container.textContent).toContain('Layer')
  })

  it('renders normally with null compareSummaries', () => {
    const dims = makeDims()
    const c = makeCluster(dims, { cluster_id: 1 })
    const { container } = render(
      createElement(ClusterTable, {
        clusters: [c],
        weights: unitWeights(1),
        axisWeights: DEFAULT_AXIS_WEIGHTS,
        compareSummaries: null,
      }),
    )
    // Table headers should still render
    expect(container.textContent).toContain('Cluster')
    expect(container.textContent).toContain('All loaded')
  })
})
