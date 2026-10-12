import { useEffect, useMemo, useState } from 'react'
import { fetchRuns } from './api'
import type { AxisWeights, Cluster, RunsResponse } from './types'
import { DEFAULT_AXIS_WEIGHTS } from './types'
import { ClusterDetailPanel } from './features/clusterDetail'
import ClusterComparePanel from './components/ClusterComparePanel'
import CompareWorkspace from './components/CompareWorkspace'
import {
  ClusterTable,
  FilterBar,
  RunSelector,
  type FilterState,
} from './features/clusters'
import WeightsPanel from './components/WeightsPanel'
import { useClusters } from './hooks/useClusters'
import { useDebouncedValue } from './hooks/useDebouncedValue'
import { useWeights } from './hooks/useWeights'
import { useScoreConfig } from './hooks/useScoreConfig'

const DEFAULT_FILTER: FilterState = {
  search: '',
  minMembers: '',
  minPain4w: '',
  dataDateFrom: '',
  dataDateTo: '',
  audience: 'all',
}

const AUDIENCE_CLASSIFIER_VERSION = 'v1'

function optionalNonNegativeInt(value: string): number | null {
  if (!value) return null
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null
}

function fmtDate(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : '—'
}

export default function App() {
  const [runs, setRuns] = useState<RunsResponse | null>(null)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [clusterRunId, setClusterRunId] = useState<number | null>(null)
  const [viabilityRunId, setViabilityRunId] = useState<number | null>(null)
  const [panelOpen, setPanelOpen] = useState(true)
  const [filter, setFilter] = useState<FilterState>(DEFAULT_FILTER)
  const [clusterSort, setClusterSort] = useState<'default' | 'burst'>('default')
  const [selectedClusterId, setSelectedClusterId] = useState<number | null>(null)
  // Show clusters that failed the within-audience eligibility gate. ON by default
  // while the multi-source candidate pool is being calibrated.
  const [showIneligible, setShowIneligible] = useState(true)

  // Compare mode (cross-run comparison)
  const [compareMode, setCompareMode] = useState(false)
  const [refRunId, setRefRunId] = useState<number | null>(null)
  const [cmpRunId, setCmpRunId] = useState<number | null>(null)

  // Legacy 14-dim weights (Score column)
  const { weights, defaults, config, setWeight, resetAll, resetGroup } = useWeights(true)
  // Path C axis weights (Explore ✦ column)
  const [axisWeights, setAxisWeights] = useState<AxisWeights>(DEFAULT_AXIS_WEIGHTS)

  const { data: scoreConfig } = useScoreConfig(true)

  useEffect(() => {
    let cancelled = false
    fetchRuns()
      .then((r) => {
        if (cancelled) return
        setRuns(r)
        setClusterRunId(r.defaults.cluster_run_id)
        setViabilityRunId(r.defaults.viability_run_id)
      })
      .catch((err) => {
        if (!cancelled) setRunsError(String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const minMembers = optionalNonNegativeInt(filter.minMembers)
  const minPain4w = optionalNonNegativeInt(filter.minPain4w)
  const debouncedSearch = useDebouncedValue(filter.search, 450)
  const refViabilityRunId = useMemo(() => {
    if (!runs || !refRunId) return null
    return runs.viability_runs.find((run) => run.cluster_run_id === refRunId)?.id ?? null
  }, [runs, refRunId])
  const cmpViabilityRunId = useMemo(() => {
    if (!runs || !cmpRunId) return null
    return runs.viability_runs.find((run) => run.cluster_run_id === cmpRunId)?.id ?? null
  }, [runs, cmpRunId])

  useEffect(() => {
    setSelectedClusterId(null)
  }, [clusterRunId, viabilityRunId, filter.dataDateFrom, filter.dataDateTo])

  const { data, loading, loadingMore, error, hasMore, loadMore } = useClusters({
    enabled: !compareMode,
    cluster_run_id: clusterRunId,
    viability_run_id: viabilityRunId,
    audience_type: filter.audience === 'all' ? undefined : filter.audience,
    include_ineligible: showIneligible,
    search: debouncedSearch,
    min_members: minMembers,
    min_pain_4w: minPain4w,
    date_from: filter.dataDateFrom || null,
    date_to: filter.dataDateTo || null,
    min_window_pain: filter.dataDateFrom || filter.dataDateTo ? 1 : null,
    sort: clusterSort,
  })

  const allClusters = data?.clusters ?? []
  const freshness = data?.freshness ?? null
  const clustersTotal = data?.total ?? allClusters.length
  const clustersTotalIsExact = data?.total_is_exact ?? true
  const filteredClusters = useMemo<Cluster[]>(() => {
    return allClusters
  }, [allClusters])

  const selectedCluster =
    selectedClusterId !== null
      ? allClusters.find((c) => c.cluster_id === selectedClusterId) ?? null
      : null
  const compareSelectedCluster =
    selectedClusterId !== null
      ? selectedCluster ?? { cluster_id: selectedClusterId, label: null } as Cluster
      : null

  return (
    <div className="flex flex-col h-full px-6 py-5 gap-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="font-serif-display text-2xl text-stone-900">
            reddit_signal
          </h1>
          <div className="text-xs text-stone-500 mt-0.5">
            Analytics · path C · pain-cluster triage (read-only)
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-xs text-stone-500">
            {data
              ? `${data.clusters.length} of ${clustersTotal}${clustersTotalIsExact ? '' : '+'} clusters · run ${data.cluster_run_id}`
              : ''}
          </div>
          <button
            type="button"
            onClick={() => setPanelOpen((o) => !o)}
            className="text-xs px-2.5 py-1 rounded-md ring-1 ring-stone-300/70 text-stone-700 hover:bg-stone-100"
          >
            {panelOpen ? 'Hide weights' : 'Show weights'}
          </button>
        </div>
      </header>

      {runs && (
        <div className="flex items-center gap-4 flex-wrap">
          <RunSelector
            clusterRuns={runs.cluster_runs}
            viabilityRuns={runs.viability_runs}
            clusterRunId={clusterRunId}
            viabilityRunId={viabilityRunId}
            onChange={({ cluster_run_id, viability_run_id }) => {
              setClusterRunId(cluster_run_id)
              setViabilityRunId(viability_run_id)
              setSelectedClusterId(null)
            }}
            compareMode={compareMode}
            refRunId={refRunId}
            cmpRunId={cmpRunId}
            onCompareChange={({ compareMode: cm, refRunId: ref, cmpRunId: cmp }) => {
              setCompareMode(cm)
              setRefRunId(ref)
              setCmpRunId(cmp)
              setSelectedClusterId(null)
            }}
          />
          {freshness && (
            <div className="basis-full text-[11px] text-stone-500">
              Map: run #{freshness.cluster_run_id} · {fmtDate(freshness.map_window_start)} →{' '}
              {fmtDate(freshness.map_window_end)} · live {fmtDate(freshness.map_live_at)}
              <span className="mx-2 text-stone-300">|</span>
              Signals: through {fmtDate(freshness.signal_data_through_day)}
              {freshness.snapshot_at ? ` · snapshot ${fmtDate(freshness.snapshot_at)}` : ''}
            </div>
          )}
        </div>
      )}
      {runsError && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-1.5">
          Failed to load runs: {runsError}
        </div>
      )}

      {!compareMode && (
        <FilterBar
          filter={filter}
          onChange={setFilter}
          showIneligible={showIneligible}
          onShowIneligibleChange={setShowIneligible}
          clusterSort={clusterSort}
          onClusterSortChange={setClusterSort}
          filteredCount={filteredClusters.length}
          totalCount={clustersTotal}
          totalIsExact={clustersTotalIsExact}
        />
      )}

      <div className="flex-1 min-h-0 flex gap-4">
        <div className="flex-1 min-w-0">
          {!compareMode && loading && !data && (
            <div className="text-sm text-stone-500">Loading clusters…</div>
          )}
          {!compareMode && error && (
            <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-1.5">
              Failed to load clusters: {error}
            </div>
          )}
          {compareMode && runs && (
            <CompareWorkspace
              enabled={compareMode}
              refRunId={refRunId}
              cmpRunId={cmpRunId}
              refViabilityRunId={refViabilityRunId}
              cmpViabilityRunId={cmpViabilityRunId}
              clusterRuns={runs.cluster_runs}
              onSelect={setSelectedClusterId}
            />
          )}
          {data && !compareMode && (
            <ClusterTable
              clusters={filteredClusters}
              weights={weights}
              axisWeights={axisWeights}
              onSelect={setSelectedClusterId}
              onLoadMore={loadMore}
              hasMore={hasMore}
              loadingMore={loadingMore}
              initialSortKey={
                clusterSort === 'burst'
                  ? 'burst_score'
                  : 'pareto_layer'
              }
              initialSortDir={clusterSort === 'default' ? 'asc' : 'desc'}
              corpusBurst={data?.corpus_burst ?? null}
              emergingOpportunities={data?.emerging_opportunities ?? []}
            />
          )}
        </div>
        {panelOpen && (
          <WeightsPanel
            config={config}
            weights={weights}
            defaults={defaults}
            onChange={setWeight}
            onResetAll={resetAll}
            onResetGroup={resetGroup}
            axisWeights={axisWeights}
            onAxisChange={setAxisWeights}
          />
        )}
      </div>

      {compareMode ? (
        <ClusterComparePanel
          cluster={compareSelectedCluster}
          open={selectedClusterId !== null}
          onClose={() => setSelectedClusterId(null)}
          refRunId={refRunId}
          cmpRunId={cmpRunId}
          viabilityRunId={cmpViabilityRunId ?? viabilityRunId}
          refViabilityRunId={refViabilityRunId}
          cmpViabilityRunId={cmpViabilityRunId}
          audienceClassifierVersion={AUDIENCE_CLASSIFIER_VERSION}
        />
      ) : (
        <ClusterDetailPanel
          cluster={selectedCluster}
          open={selectedClusterId !== null}
          onClose={() => setSelectedClusterId(null)}
          clusterRunId={clusterRunId}
          viabilityRunId={viabilityRunId}
          audienceClassifierVersion={AUDIENCE_CLASSIFIER_VERSION}
          scoreConfig={scoreConfig}
          dateFrom={filter.dataDateFrom || null}
          dateTo={filter.dataDateTo || null}
        />
      )}
    </div>
  )
}
