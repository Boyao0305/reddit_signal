import type {
  ClusterCompareResponse,
  ClusterCompareSummary,
  ClusterDetail,
  ClusterFilterOptionsResponse,
  ClustersResponse,
  CorpusVelocityResponse,
  MembersResponse,
  VelocityResponse,
} from '../types'
import { getCachedJSON, getJSON } from './client'

export function fetchClusters(params: {
  cluster_run_id: number
  viability_run_id: number
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
  page?: number
  page_size?: number
}): Promise<ClustersResponse> {
  const qs = new URLSearchParams({
    cluster_run_id: String(params.cluster_run_id),
    viability_run_id: String(params.viability_run_id),
    audience_classifier_version: params.audience_classifier_version ?? 'v1',
    audience_type: params.audience_type ?? 'all',
    include_ineligible: String(params.include_ineligible ?? true),
    sort: params.sort ?? 'default',
    page: String(params.page ?? 1),
    page_size: String(params.page_size ?? 50),
  })
  const search = params.search?.trim()
  if (search) qs.set('search', search)
  if (params.min_members !== null && params.min_members !== undefined) {
    qs.set('min_members', String(params.min_members))
  }
  if (params.min_pain_4w !== null && params.min_pain_4w !== undefined) {
    qs.set('min_pain_4w', String(params.min_pain_4w))
  }
  if (params.date_from) qs.set('date_from', params.date_from)
  if (params.date_to) qs.set('date_to', params.date_to)
  if (params.subreddits && params.subreddits.length > 0) {
    qs.set('subreddits', params.subreddits.join(','))
  }
  if (params.min_window_pain !== null && params.min_window_pain !== undefined) {
    qs.set('min_window_pain', String(params.min_window_pain))
  }
  return getJSON<ClustersResponse>(`/api/v2/clusters?${qs.toString()}`)
}

export function fetchClusterDetail(params: {
  cluster_id: number
  cluster_run_id: number
  viability_run_id: number
  audience_classifier_version?: string
}): Promise<ClusterDetail> {
  const qs = new URLSearchParams({
    cluster_run_id: String(params.cluster_run_id),
    viability_run_id: String(params.viability_run_id),
    audience_classifier_version: params.audience_classifier_version ?? 'v1',
  })
  return getCachedJSON<ClusterDetail>(
    `/api/v2/clusters/${params.cluster_id}?${qs.toString()}`,
  )
}

export function fetchVelocity(params: {
  cluster_id: number
  cluster_run_id: number
}): Promise<VelocityResponse> {
  const qs = new URLSearchParams({ cluster_run_id: String(params.cluster_run_id) })
  return getCachedJSON<VelocityResponse>(
    `/api/v2/clusters/${params.cluster_id}/velocity?${qs.toString()}`,
  )
}

export function fetchMembers(params: {
  cluster_id: number
  page: number
  page_size?: number
  date_from?: string | null
  date_to?: string | null
  subreddits?: string[]
  source?: string | null
}): Promise<MembersResponse> {
  const qs = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.page_size ?? 20),
  })
  if (params.date_from) qs.set('date_from', params.date_from)
  if (params.date_to) qs.set('date_to', params.date_to)
  if (params.subreddits && params.subreddits.length > 0) {
    qs.set('subreddits', params.subreddits.join(','))
  }
  if (params.source && params.source !== 'all') qs.set('source', params.source)
  return getCachedJSON<MembersResponse>(
    `/api/v2/clusters/${params.cluster_id}/members?${qs.toString()}`,
  )
}

export function fetchClusterFilterOptions(params: {
  cluster_run_id: number
}): Promise<ClusterFilterOptionsResponse> {
  const qs = new URLSearchParams({ cluster_run_id: String(params.cluster_run_id) })
  return getCachedJSON<ClusterFilterOptionsResponse>(
    `/api/v2/cluster-filter-options?${qs.toString()}`,
  )
}

export function fetchCorpusVelocity(params: {
  cluster_run_id: number
}): Promise<CorpusVelocityResponse> {
  const qs = new URLSearchParams({ cluster_run_id: String(params.cluster_run_id) })
  return getCachedJSON<CorpusVelocityResponse>(`/api/v2/corpus-velocity?${qs.toString()}`)
}

// ── cluster cross-run compare ─────────────────────────────────────────────────

export function fetchClusterCompare(params: {
  cluster_id: number
  ref_run_id: number
  cmp_run_id: number
  viability_run_id: number
  ref_viability_run_id?: number | null
  cmp_viability_run_id?: number | null
  audience_classifier_version?: string
}): Promise<ClusterCompareResponse> {
  const qs = new URLSearchParams({
    ref_run_id: String(params.ref_run_id),
    cmp_run_id: String(params.cmp_run_id),
    viability_run_id: String(params.viability_run_id),
    audience_classifier_version: params.audience_classifier_version ?? 'v1',
  })
  if (params.ref_viability_run_id !== null && params.ref_viability_run_id !== undefined) {
    qs.set('ref_viability_run_id', String(params.ref_viability_run_id))
  }
  if (params.cmp_viability_run_id !== null && params.cmp_viability_run_id !== undefined) {
    qs.set('cmp_viability_run_id', String(params.cmp_viability_run_id))
  }
  return getJSON<ClusterCompareResponse>(
    `/api/v2/clusters/${params.cluster_id}/compare?${qs.toString()}`,
  )
}

export function fetchClusterCompareSummaries(params: {
  ref_run_id: number
  cmp_run_id: number
  viability_run_id: number
  ref_viability_run_id?: number | null
  cmp_viability_run_id?: number | null
  audience_classifier_version?: string
  cluster_ids?: number[]
}): Promise<ClusterCompareSummary[]> {
  const qs = new URLSearchParams({
    ref_run_id: String(params.ref_run_id),
    cmp_run_id: String(params.cmp_run_id),
    viability_run_id: String(params.viability_run_id),
    audience_classifier_version: params.audience_classifier_version ?? 'v1',
  })
  if (params.ref_viability_run_id !== null && params.ref_viability_run_id !== undefined) {
    qs.set('ref_viability_run_id', String(params.ref_viability_run_id))
  }
  if (params.cmp_viability_run_id !== null && params.cmp_viability_run_id !== undefined) {
    qs.set('cmp_viability_run_id', String(params.cmp_viability_run_id))
  }
  if (params.cluster_ids && params.cluster_ids.length > 0) {
    qs.set('cluster_ids', params.cluster_ids.join(','))
  }
  return getJSON<ClusterCompareSummary[]>(
    `/api/v2/cluster-compare-summaries?${qs.toString()}`,
  )
}
