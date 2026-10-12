import { useEffect, useState } from 'react'
import { fetchClusters } from '../api'
import type { Cluster, ClustersResponse } from '../types'

const PAGE_SIZE = 50

export interface UseClustersResult {
  data: ClustersResponse | null
  loading: boolean
  loadingMore: boolean
  error: string | null
  hasMore: boolean
  loadMore: () => void
}

export function useClusters(params: {
  enabled?: boolean
  cluster_run_id: number | null
  viability_run_id: number | null
  audience_classifier_version?: string
  audience_type?: string
  include_ineligible?: boolean
  search?: string
  min_members?: number | null
  min_pain_4w?: number | null
  date_from?: string | null
  date_to?: string | null
  subreddits?: string[]
  min_window_pain?: number | null
  sort?: 'default' | 'burst' | 'window_pain'
  page_size?: number
}): UseClustersResult {
  const [data, setData] = useState<ClustersResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  const cri = params.cluster_run_id
  const vri = params.viability_run_id
  const acv = params.audience_classifier_version ?? 'v1'
  const at = params.audience_type ?? 'all'
  const includeIneligible = params.include_ineligible ?? true
  const search = params.search ?? ''
  const minMembers = params.min_members ?? null
  const minPain4w = params.min_pain_4w ?? null
  const dateFrom = params.date_from ?? null
  const dateTo = params.date_to ?? null
  const selectedSubreddits = params.subreddits ?? []
  const selectedSubredditsKey = selectedSubreddits.slice().sort().join(',')
  const minWindowPain = params.min_window_pain ?? null
  const sort = params.sort ?? 'default'
  const pageSize = params.page_size ?? PAGE_SIZE
  const enabled = params.enabled ?? true

  useEffect(() => {
    setPage(1)
  }, [
    enabled,
    cri,
    vri,
    acv,
    at,
    includeIneligible,
    search,
    minMembers,
    minPain4w,
    dateFrom,
    dateTo,
    selectedSubredditsKey,
    minWindowPain,
    sort,
    pageSize,
  ])

  useEffect(() => {
    if (!enabled || cri === null || vri === null) {
      setData(null)
      setError(null)
      setLoading(false)
      setLoadingMore(false)
      return
    }
    let cancelled = false
    if (page === 1) {
      setLoading(true)
      setData(null)
    } else {
      setLoadingMore(true)
    }
    setError(null)
    fetchClusters({
      cluster_run_id: cri,
      viability_run_id: vri,
      audience_classifier_version: acv,
      audience_type: at,
      include_ineligible: includeIneligible,
      search,
      min_members: minMembers,
      min_pain_4w: minPain4w,
      date_from: dateFrom,
      date_to: dateTo,
      subreddits: selectedSubreddits,
      min_window_pain: minWindowPain,
      sort,
      page,
      page_size: pageSize,
    })
      .then((d) => {
        if (cancelled) return
        setData((prev) => {
          if (page === 1 || prev === null) return d
          const seen = new Set(prev.clusters.map((c) => c.cluster_id))
          const nextClusters: Cluster[] = prev.clusters.concat(
            d.clusters.filter((c) => !seen.has(c.cluster_id)),
          )
          return { ...d, clusters: nextClusters }
        })
      })
      .catch((err) => {
        if (!cancelled) setError(String(err))
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
          setLoadingMore(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [
    enabled,
    cri,
    vri,
    acv,
    at,
    includeIneligible,
    search,
    minMembers,
    minPain4w,
    dateFrom,
    dateTo,
    selectedSubredditsKey,
    minWindowPain,
    sort,
    pageSize,
    page,
  ])

  const hasMore = data?.has_more ?? false

  function loadMore() {
    if (loading || loadingMore || !hasMore) return
    setPage((p) => p + 1)
  }

  return { data, loading, loadingMore, error, hasMore, loadMore }
}
