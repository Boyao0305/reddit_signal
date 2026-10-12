import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchClusterCompareSummaries } from '../api/clusters'
import type {
  Cluster,
  ClusterCompareSummary,
  ClusterRun,
} from '../types'
import { fmtNumber, fmtPercent, truncate } from '../shared/format'
import { useClusters, type UseClustersResult } from '../hooks/useClusters'

const EMPTY_CLUSTERS: Cluster[] = []

interface CompareWorkspaceProps {
  enabled: boolean
  refRunId: number | null
  cmpRunId: number | null
  refViabilityRunId: number | null
  cmpViabilityRunId: number | null
  clusterRuns: ClusterRun[]
  onSelect: (clusterId: number) => void
}

interface CompareRow {
  cluster: Cluster
  summary: ClusterCompareSummary | null
  counterpartId: number | null
  status: string
  tone: 'matched' | 'new' | 'dropped' | 'changed'
  detail: string
}

function runLabel(runs: ClusterRun[], id: number | null): string {
  const run = runs.find((r) => r.id === id)
  if (!run) return id ? `#${id}` : '-'
  return `#${run.id} · ${run.window_start ?? '?'} -> ${run.window_end ?? '?'}`
}

function displayLayer(c: Cluster | null): number | null {
  return c?.competition_adjusted_pareto_layer ?? c?.pareto_layer ?? null
}

function displayBalance(c: Cluster | null): number | null {
  return c?.competition_adjusted_balance_score ?? c?.balance_score ?? null
}

function summaryTone(summary: ClusterCompareSummary | null): CompareRow['tone'] {
  if (!summary?.matched) return 'changed'
  const layer = summary.pareto_layer_delta.absolute_change
  const balance = summary.balance_score_delta.absolute_change
  if ((layer !== null && layer !== 0) || (balance !== null && Math.abs(balance) >= 0.04)) {
    return 'changed'
  }
  return 'matched'
}

function deltaText(summary: ClusterCompareSummary | null): string {
  if (!summary?.matched) return 'no lineage match'
  const parts: string[] = []
  const layer = summary.pareto_layer_delta.absolute_change
  if (layer !== null && layer !== 0) {
    parts.push(layer < 0 ? `layer +${Math.abs(layer)}` : `layer -${layer}`)
  }
  const balance = summary.balance_score_delta.absolute_change
  if (balance !== null && Math.abs(balance) >= 0.01) {
    parts.push(`balance ${balance >= 0 ? '+' : ''}${balance.toFixed(2)}`)
  }
  const members = summary.member_count_delta.absolute_change
  if (members !== null && Math.abs(members) >= 5) {
    parts.push(`members ${members >= 0 ? '+' : ''}${fmtNumber(members)}`)
  }
  return parts.length ? parts.join(' · ') : 'matched'
}

function useAutoLoadAll(result: UseClustersResult, enabled: boolean) {
  const requestedRef = useRef(false)

  useEffect(() => {
    if (!enabled || !result.hasMore) {
      requestedRef.current = false
      return
    }
    if (result.loading || result.loadingMore || requestedRef.current) return
    requestedRef.current = true
    result.loadMore()
  }, [
    enabled,
    result.hasMore,
    result.loading,
    result.loadingMore,
    result.data?.clusters.length,
    result.loadMore,
  ])

  useEffect(() => {
    if (result.loading || result.loadingMore) {
      requestedRef.current = false
    }
  }, [result.loading, result.loadingMore])
}

function statusClass(tone: CompareRow['tone']): string {
  if (tone === 'new') return 'bg-sky-50 text-sky-700 ring-sky-200'
  if (tone === 'dropped') return 'bg-amber-50 text-amber-700 ring-amber-200'
  if (tone === 'changed') return 'bg-violet-50 text-violet-700 ring-violet-200'
  return 'bg-emerald-50 text-emerald-700 ring-emerald-200'
}

function CompareTable({
  title,
  subtitle,
  rows,
  loading,
  side,
  onSelect,
}: {
  title: string
  subtitle: string
  rows: CompareRow[]
  loading: boolean
  side: 'ref' | 'cmp'
  onSelect: (clusterId: number) => void
}) {
  return (
    <section className="min-h-0 border-t border-stone-200 pt-3">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-stone-900">{title}</h3>
          <div className="truncate text-[11px] text-stone-400">{subtitle}</div>
        </div>
        <div className="shrink-0 text-xs tabular-nums text-stone-500">
          {loading ? 'Loading...' : `${fmtNumber(rows.length)} rows`}
        </div>
      </div>
      <div className="min-h-0 overflow-auto">
        <table className="min-w-full table-fixed text-left text-xs">
          <thead className="sticky top-0 z-10 bg-white text-[10px] font-semibold uppercase tracking-[0.08em] text-stone-400">
            <tr className="border-b border-stone-200">
              <th className="w-[38%] py-2 pr-3">Cluster</th>
              <th className="w-[19%] py-2 pr-3">Match</th>
              <th className="w-[10%] py-2 pr-3">Layer</th>
              <th className="w-[12%] py-2 pr-3">Balance</th>
              <th className="w-[10%] py-2 pr-3">Pain 4w</th>
              <th className="w-[11%] py-2">Members</th>
            </tr>
          </thead>
          <tbody>
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={6} className="py-6 text-center text-sm text-stone-400">
                  No clusters in this run.
                </td>
              </tr>
            )}
            {rows.map((row) => {
              const layer = displayLayer(row.cluster)
              const balance = displayBalance(row.cluster)
              return (
                <tr
                  key={`${side}:${row.cluster.cluster_id}`}
                  onClick={() => onSelect(row.cluster.cluster_id)}
                  className="cursor-pointer border-b border-stone-100 hover:bg-stone-50"
                >
                  <td className="py-2 pr-3 align-top">
                    <div className="truncate font-medium text-stone-900">
                      {row.cluster.label ?? `Cluster ${row.cluster.cluster_id}`}
                    </div>
                    <div className="truncate text-[11px] text-stone-400">
                      #{row.cluster.cluster_id} · {truncate(row.cluster.v_rationale, 96)}
                    </div>
                  </td>
                  <td className="py-2 pr-3 align-top">
                    <span className={`inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ${statusClass(row.tone)}`}>
                      {row.status}
                    </span>
                    <div className="mt-1 truncate text-[11px] text-stone-400">
                      {row.counterpartId ? `#${row.counterpartId} · ` : ''}{row.detail}
                    </div>
                  </td>
                  <td className="py-2 pr-3 align-top tabular-nums text-stone-700">
                    {layer ? `L${layer}` : '-'}
                  </td>
                  <td className="py-2 pr-3 align-top tabular-nums text-stone-700">
                    {fmtPercent(balance)}
                  </td>
                  <td className="py-2 pr-3 align-top tabular-nums text-stone-700">
                    {fmtNumber(row.cluster.pain_count_last_4w)}
                  </td>
                  <td className="py-2 align-top tabular-nums text-stone-700">
                    {fmtNumber(row.cluster.member_count)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function CompareWorkspace({
  enabled,
  refRunId,
  cmpRunId,
  refViabilityRunId,
  cmpViabilityRunId,
  clusterRuns,
  onSelect,
}: CompareWorkspaceProps) {
  const refData = useClusters({
    enabled,
    cluster_run_id: refRunId,
    viability_run_id: refViabilityRunId,
    audience_type: 'all',
    include_ineligible: true,
    sort: 'default',
    page_size: 50,
  })
  const cmpData = useClusters({
    enabled,
    cluster_run_id: cmpRunId,
    viability_run_id: cmpViabilityRunId,
    audience_type: 'all',
    include_ineligible: true,
    sort: 'default',
    page_size: 50,
  })
  useAutoLoadAll(refData, enabled)
  useAutoLoadAll(cmpData, enabled)

  const [summaries, setSummaries] = useState<Record<number, ClusterCompareSummary>>({})
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)

  useEffect(() => {
    if (!enabled || !refRunId || !cmpRunId || !cmpViabilityRunId) {
      setSummaries({})
      return
    }
    let cancelled = false
    setSummaryError(null)
    setSummaryLoading(true)
    fetchClusterCompareSummaries({
      ref_run_id: refRunId,
      cmp_run_id: cmpRunId,
      viability_run_id: cmpViabilityRunId,
      ref_viability_run_id: refViabilityRunId,
      cmp_viability_run_id: cmpViabilityRunId,
    })
      .then((rows) => {
        if (cancelled) return
        const next: Record<number, ClusterCompareSummary> = {}
        for (const row of rows) {
          if (row.cmp_cluster_id !== null && row.cmp_cluster_id !== undefined) {
            next[row.cmp_cluster_id] = row
          }
        }
        setSummaries(next)
        setSummaryLoading(false)
      })
      .catch((err) => {
        if (!cancelled) {
          setSummaries({})
          setSummaryError(String(err))
          setSummaryLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [enabled, refRunId, cmpRunId, refViabilityRunId, cmpViabilityRunId])

  const refClusters = refData.data?.clusters ?? EMPTY_CLUSTERS
  const cmpClusters = cmpData.data?.clusters ?? EMPTY_CLUSTERS

  const refRows = useMemo<CompareRow[]>(() => {
    const matchedByRef = new Map<number, ClusterCompareSummary>()
    for (const summary of Object.values(summaries)) {
      if (summary.matched && summary.ref_cluster_id !== null && summary.ref_cluster_id !== undefined) {
        matchedByRef.set(summary.ref_cluster_id, summary)
      }
    }
    return refClusters.map((cluster) => {
      const summary = matchedByRef.get(cluster.cluster_id) ?? null
      const counterpartId = summary?.cmp_cluster_id ?? null
      return {
        cluster,
        summary,
        counterpartId,
        status: summary?.matched ? 'matched in cmp' : 'not in cmp',
        tone: summary?.matched ? summaryTone(summary) : 'dropped',
        detail: summary?.matched ? deltaText(summary) : 'no lineage match',
      }
    })
  }, [refClusters, summaries])

  const cmpRows = useMemo<CompareRow[]>(() => {
    return cmpClusters.map((cluster) => {
      const summary = summaries[cluster.cluster_id] ?? null
      const counterpartId = summary?.matched ? summary.ref_cluster_id ?? null : null
      return {
        cluster,
        summary,
        counterpartId,
        status: summary?.matched ? 'matched from ref' : 'new in cmp',
        tone: summary?.matched ? summaryTone(summary) : 'new',
        detail: summary?.matched ? deltaText(summary) : 'no lineage match',
      }
    })
  }, [cmpClusters, summaries])

  const loading = refData.loading || cmpData.loading || refData.loadingMore || cmpData.loadingMore || summaryLoading
  const error = refData.error || cmpData.error || summaryError

  return (
    <div className="flex h-full min-h-0 flex-col rounded-[20px] bg-white p-4 ring-1 ring-stone-200/60 shadow-[0_1px_3px_rgba(28,25,23,0.05),0_4px_14px_rgba(28,25,23,0.04)]">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-stone-400">
            Compare runs
          </div>
          <div className="mt-1 text-sm text-stone-800">
            <span className="text-stone-500">Ref</span> {runLabel(clusterRuns, refRunId)}
            <span className="mx-2 text-stone-300">{'->'}</span>
            <span className="text-stone-500">Cmp</span> {runLabel(clusterRuns, cmpRunId)}
          </div>
        </div>
        <div className="text-right text-xs text-stone-500">
          {loading ? 'Loading compare...' : `${fmtNumber(refRows.length)} ref · ${fmtNumber(cmpRows.length)} cmp`}
        </div>
      </div>
      {error && (
        <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-sm text-red-700">
          Failed to load compare workspace: {error}
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-2">
        <CompareTable
          title="Reference run"
          subtitle={runLabel(clusterRuns, refRunId)}
          rows={refRows}
          loading={loading && refRows.length === 0}
          side="ref"
          onSelect={onSelect}
        />
        <CompareTable
          title="Comparison run"
          subtitle={runLabel(clusterRuns, cmpRunId)}
          rows={cmpRows}
          loading={loading && cmpRows.length === 0}
          side="cmp"
          onSelect={onSelect}
        />
      </div>
    </div>
  )
}
