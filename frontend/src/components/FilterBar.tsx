import { useEffect, useState } from 'react'

export type AudienceFilter = 'all' | 'B2B' | 'B2C' | 'mixed' | 'unclear'

export interface FilterState {
  search: string
  minMembers: string
  minPain4w: string
  dataDateFrom: string
  dataDateTo: string
  audience: AudienceFilter
}

export interface FilterBarProps {
  filter: FilterState
  onChange: (f: FilterState) => void
  showIneligible: boolean
  onShowIneligibleChange: (value: boolean) => void
  clusterSort: 'default' | 'burst'
  onClusterSortChange: (value: 'default' | 'burst') => void
  filteredCount: number
  totalCount: number
  totalIsExact?: boolean
}

export default function FilterBar({
  filter,
  onChange,
  showIneligible,
  onShowIneligibleChange,
  clusterSort,
  onClusterSortChange,
  filteredCount,
  totalCount,
  totalIsExact = true,
}: FilterBarProps) {
  const [expanded, setExpanded] = useState(true)
  const [dataDateDraft, setDataDateDraft] = useState({
    from: filter.dataDateFrom,
    to: filter.dataDateTo,
  })

  useEffect(() => {
    setDataDateDraft({
      from: filter.dataDateFrom,
      to: filter.dataDateTo,
    })
  }, [filter.dataDateFrom, filter.dataDateTo])

  function update<K extends keyof FilterState>(key: K, value: FilterState[K]) {
    onChange({ ...filter, [key]: value })
  }

  function applyDataDateRange() {
    onChange({
      ...filter,
      dataDateFrom: dataDateDraft.from,
      dataDateTo: dataDateDraft.to,
    })
  }

  function clearDataDateRange() {
    setDataDateDraft({ from: '', to: '' })
    onChange({
      ...filter,
      dataDateFrom: '',
      dataDateTo: '',
    })
  }

  const dataDateChanged =
    dataDateDraft.from !== filter.dataDateFrom || dataDateDraft.to !== filter.dataDateTo

  const inputClass =
    'h-8 px-3 rounded-lg ring-1 ring-stone-200 bg-stone-50 text-sm text-stone-800 placeholder-stone-400 focus:outline-none focus:ring-stone-400 transition'
  const buttonClass =
    'h-8 px-2.5 rounded-lg text-xs ring-1 transition whitespace-nowrap'
  const activeAdvancedCount = [
    filter.minMembers,
    filter.minPain4w,
    filter.dataDateFrom || filter.dataDateTo,
    showIneligible,
    clusterSort === 'burst',
  ].filter(Boolean).length

  return (
    <div className="bg-white rounded-[20px] ring-1 ring-stone-200/60 shadow-[0_1px_3px_rgba(28,25,23,0.05),0_4px_14px_rgba(28,25,23,0.04)] px-5 py-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-[260px]">
          <input
            type="text"
            value={filter.search}
            onChange={(e) => update('search', e.target.value)}
            placeholder="Search cluster label…"
            className={`${inputClass} w-full`}
            aria-label="Search clusters"
          />
        </div>
        <label className="flex items-center gap-1.5 text-xs text-stone-500">
          Audience
          <select
            value={filter.audience}
            onChange={(e) => update('audience', e.target.value as AudienceFilter)}
            className={`${inputClass} pr-8`}
          >
            <option value="all">All</option>
            <option value="B2B">B2B</option>
            <option value="B2C">B2C</option>
            <option value="mixed">Mixed</option>
            <option value="unclear">Unclear</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          className={`${buttonClass} ${
            expanded
              ? 'bg-stone-900 text-white ring-stone-900 hover:bg-stone-800'
              : 'text-stone-700 ring-stone-300/70 hover:bg-stone-100'
          }`}
        >
          {expanded ? 'Hide filters' : `Filters${activeAdvancedCount ? ` (${activeAdvancedCount})` : ''}`}
        </button>
        <div className="text-[11px] text-stone-500 tabular-nums ml-auto whitespace-nowrap">
          {filteredCount === totalCount && totalIsExact
            ? `${totalCount} clusters`
            : totalIsExact
              ? `${filteredCount} of ${totalCount} clusters`
              : `${filteredCount} of ${totalCount}+ clusters`}
        </div>
      </div>

      {expanded && (
        <div className="mt-3 pt-3 border-t border-stone-100 flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1.5 text-xs text-stone-500">
            Min members
            <input
              type="number"
              min={0}
              value={filter.minMembers}
              onChange={(e) => update('minMembers', e.target.value)}
              className={`${inputClass} w-20`}
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-stone-500">
            Min pain 4w
            <input
              type="number"
              min={0}
              value={filter.minPain4w}
              onChange={(e) => update('minPain4w', e.target.value)}
              className={`${inputClass} w-20`}
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-stone-500">
            Data from
            <input
              type="date"
              value={dataDateDraft.from}
              onChange={(e) => setDataDateDraft((current) => ({
                ...current,
                from: e.target.value,
              }))}
              className={`${inputClass} w-36`}
              aria-label="Data date range start"
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-stone-500">
            Data to
            <input
              type="date"
              value={dataDateDraft.to}
              onChange={(e) => setDataDateDraft((current) => ({
                ...current,
                to: e.target.value,
              }))}
              className={`${inputClass} w-36`}
              aria-label="Data date range end"
            />
          </label>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={applyDataDateRange}
              disabled={!dataDateChanged}
              className={`${buttonClass} ${
                dataDateChanged
                  ? 'bg-stone-900 text-white ring-stone-900 hover:bg-stone-800'
                  : 'bg-stone-50 text-stone-400 ring-stone-200 cursor-default'
              }`}
            >
              Apply
            </button>
            {(filter.dataDateFrom || filter.dataDateTo || dataDateDraft.from || dataDateDraft.to) && (
              <button
                type="button"
                onClick={clearDataDateRange}
                className={`${buttonClass} ring-stone-200 text-stone-600 hover:bg-stone-50`}
              >
                Clear
              </button>
            )}
          </div>
          <label className="flex items-center gap-1.5 text-xs text-stone-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showIneligible}
              onChange={(e) => onShowIneligibleChange(e.target.checked)}
              className="accent-stone-600"
            />
            Show ineligible
          </label>
          <button
            type="button"
            onClick={() => onClusterSortChange(clusterSort === 'burst' ? 'default' : 'burst')}
            className={`${buttonClass} ${
              clusterSort === 'burst'
                ? 'bg-rose-50 text-rose-700 ring-rose-200'
                : 'text-stone-700 ring-stone-300/70 hover:bg-stone-100'
            }`}
          >
            {clusterSort === 'burst' ? 'Burst sort on' : 'Sort by burst'}
          </button>
        </div>
      )}

      {(filter.dataDateFrom || filter.dataDateTo) && (
        <div className="mt-2 text-[11px] text-stone-400">
          Data window: {filter.dataDateFrom || 'start'} → {filter.dataDateTo || 'latest'}
        </div>
      )}
    </div>
  )
}
