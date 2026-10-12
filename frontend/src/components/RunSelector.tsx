import type { ClusterRun, ViabilityRun } from '../types'

export interface RunSelectorProps {
  clusterRuns: ClusterRun[]
  viabilityRuns: ViabilityRun[]
  clusterRunId: number | null
  viabilityRunId: number | null
  onChange: (next: { cluster_run_id: number; viability_run_id: number }) => void
  // Compare mode (cross-run)
  compareMode?: boolean
  refRunId?: number | null
  cmpRunId?: number | null
  onCompareChange?: (next: {
    compareMode: boolean
    refRunId: number | null
    cmpRunId: number | null
  }) => void
}

export default function RunSelector({
  clusterRuns,
  viabilityRuns,
  clusterRunId,
  viabilityRunId,
  onChange,
  compareMode = false,
  refRunId: extRefRunId,
  cmpRunId: extCmpRunId,
  onCompareChange,
}: RunSelectorProps) {
  const selectedCluster = clusterRuns.find((r) => r.id === clusterRunId) ?? null
  const compatibleViab = viabilityRuns.filter((v) => v.cluster_run_id === clusterRunId)

  function handleClusterChange(nextId: number) {
    const v = viabilityRuns.find((vr) => vr.cluster_run_id === nextId)
    onChange({
      cluster_run_id: nextId,
      viability_run_id: v ? v.id : (viabilityRunId ?? 0),
    })
  }

  function handleViabChange(nextId: number) {
    onChange({
      cluster_run_id: clusterRunId ?? 0,
      viability_run_id: nextId,
    })
  }

  const liveRun = clusterRuns.find((r) => r.status === 'live') ?? null
  const showWarning =
    !compareMode && selectedCluster !== null && selectedCluster.status !== 'live'

  // Sort runs by window_start descending for display
  const sortedRuns = [...clusterRuns].sort((a, b) => {
    const aW = a.window_start ?? ''
    const bW = b.window_start ?? ''
    return bW.localeCompare(aW)
  })

  // For compare mode: derive ref / cmp run IDs
  const refRunId = compareMode ? extRefRunId : null
  const cmpRunId = compareMode ? extCmpRunId : null

  function handleToggleCompare(on: boolean) {
    if (!onCompareChange) return
    if (on) {
      // Defaults: ref = newest non-live historical run, cmp = live run
      const nonLive = sortedRuns.filter((r) => r.status !== 'live')
      const ref = nonLive.length > 0 ? nonLive[0] : sortedRuns[0] ?? null
      const cmp = liveRun ?? sortedRuns[0] ?? null
      onCompareChange({
        compareMode: true,
        refRunId: ref?.id ?? null,
        cmpRunId: cmp?.id ?? null,
      })
    } else {
      onCompareChange({ compareMode: false, refRunId: null, cmpRunId: null })
    }
  }

  function handleRefChange(nextId: number) {
    if (!onCompareChange) return
    onCompareChange({
      compareMode: true,
      refRunId: nextId,
      cmpRunId: cmpRunId ?? null,
    })
  }

  function handleCmpChange(nextId: number) {
    if (!onCompareChange) return
    onCompareChange({
      compareMode: true,
      refRunId: refRunId ?? null,
      cmpRunId: nextId,
    })
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Normal mode — existing single-selector layout */}
      {!compareMode && (
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <span className="text-gray-600">Cluster run</span>
            <select
              className="border border-gray-300 rounded px-2 py-1 bg-white"
              value={clusterRunId ?? ''}
              onChange={(e) => handleClusterChange(Number(e.target.value))}
            >
              {clusterRuns.map((r) => (
                <option key={r.id} value={r.id}>
                  #{r.id} · {r.window_start ?? '?'} → {r.window_end ?? '?'} ·{' '}
                  {r.status}
                  {r.live_at ? ` · live ${r.live_at.slice(0, 10)}` : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-gray-600">Viability run</span>
            <select
              className="border border-gray-300 rounded px-2 py-1 bg-white"
              value={viabilityRunId ?? ''}
              onChange={(e) => handleViabChange(Number(e.target.value))}
            >
              {compatibleViab.map((v) => (
                <option key={v.id} value={v.id}>
                  #{v.id} · {v.llm_model ?? '?'} · n={v.n_clusters ?? '?'}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {/* Compare mode toggle */}
      {onCompareChange && (
        <div className="flex items-center gap-2 text-sm">
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={compareMode}
              onChange={(e) => handleToggleCompare(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-stone-700 focus:ring-stone-500"
            />
            <span className="text-gray-600">Compare mode</span>
          </label>
        </div>
      )}

      {/* Compare mode — dual run selectors */}
      {compareMode && (
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <span className="text-gray-500 text-xs">Ref</span>
            <select
              className="border border-gray-300 rounded px-2 py-1 bg-white text-xs"
              value={refRunId ?? ''}
              onChange={(e) => handleRefChange(Number(e.target.value))}
            >
              {sortedRuns
                .filter((r) => r.id !== cmpRunId)
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} · {r.window_start ?? '?'} → {r.window_end ?? '?'} ·{' '}
                    {r.status}
                  </option>
                ))}
            </select>
          </label>
          <span className="text-gray-400 text-xs">vs</span>
          <label className="flex items-center gap-1.5">
            <span className="text-gray-500 text-xs">Cmp</span>
            <select
              className="border border-gray-300 rounded px-2 py-1 bg-white text-xs"
              value={cmpRunId ?? ''}
              onChange={(e) => handleCmpChange(Number(e.target.value))}
            >
              {sortedRuns
                .filter((r) => r.id !== refRunId)
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} · {r.window_start ?? '?'} → {r.window_end ?? '?'} ·{' '}
                    {r.status}
                  </option>
                ))}
            </select>
          </label>
        </div>
      )}

      {showWarning && (
        <div className="bg-amber-50 text-amber-900 border border-amber-200 rounded px-3 py-1.5 text-sm">
          Historical map — cluster shapes come from{' '}
          <strong>{selectedCluster?.window_end ?? '?'}</strong>; signal freshness is shown above.
          {liveRun && (
            <>
              {' '}
              <button
                type="button"
                className="underline font-medium hover:text-amber-700"
                onClick={() => handleClusterChange(liveRun.id)}
              >
                Switch to live run
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
