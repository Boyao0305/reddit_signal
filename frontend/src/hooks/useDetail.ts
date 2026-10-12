import { useEffect, useState } from 'react'
import {
  fetchClusterDetail,
  fetchCorpusVelocity,
  fetchMembers,
  fetchVelocity,
} from '../api'
import type {
  ClusterDetail,
  CorpusVelocityResponse,
  MembersResponse,
  VelocityResponse,
} from '../types'

export interface UseDetailResult {
  detail: ClusterDetail | null
  velocity: VelocityResponse | null
  members: MembersResponse | null
  membersPage: number
  setMembersPage: (p: number) => void
  corpusVelocity: CorpusVelocityResponse | null
  loading: boolean
  error: string | null
}

export function useDetail(params: {
  cluster_id: number | null
  cluster_run_id: number | null
  viability_run_id: number | null
  audience_classifier_version?: string
  date_from?: string | null
  date_to?: string | null
  subreddits?: string[]
  source?: string | null
}): UseDetailResult {
  const { cluster_id, cluster_run_id, viability_run_id } = params
  const audienceClassifierVersion = params.audience_classifier_version ?? 'v1'
  const dateFrom = params.date_from ?? null
  const dateTo = params.date_to ?? null
  const subreddits = params.subreddits ?? []
  const subredditsKey = subreddits.slice().sort().join(',')
  const source = params.source ?? 'all'

  const [detail, setDetail] = useState<ClusterDetail | null>(null)
  const [velocity, setVelocity] = useState<VelocityResponse | null>(null)
  const [corpusVelocity, setCorpusVelocity] = useState<CorpusVelocityResponse | null>(null)
  const [members, setMembers] = useState<MembersResponse | null>(null)
  const [membersPage, setMembersPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset whenever cluster changes.
  useEffect(() => {
    setDetail(null)
    setVelocity(null)
    setCorpusVelocity(null)
    setMembers(null)
    setMembersPage(1)
    setError(null)
  }, [cluster_id, dateFrom, dateTo, subredditsKey, source])

  // Primary load: detail + velocity + corpus + first members page.
  useEffect(() => {
    if (cluster_id === null || cluster_run_id === null || viability_run_id === null) return
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      fetchClusterDetail({
        cluster_id,
        cluster_run_id,
        viability_run_id,
        audience_classifier_version: audienceClassifierVersion,
      }),
      fetchVelocity({ cluster_id, cluster_run_id }),
      fetchCorpusVelocity({ cluster_run_id }),
      fetchMembers({ cluster_id, page: 1, page_size: 20, date_from: dateFrom, date_to: dateTo, subreddits, source }),
    ])
      .then(([d, v, cv, m]) => {
        if (cancelled) return
        setDetail(d)
        setVelocity(v)
        setCorpusVelocity(cv)
        setMembers(m)
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
  }, [cluster_id, cluster_run_id, viability_run_id, audienceClassifierVersion, dateFrom, dateTo, subredditsKey, source])

  // Members re-fetch when page changes (skip page 1 — already loaded).
  useEffect(() => {
    if (cluster_id === null) return
    if (membersPage === 1 && members && members.page === 1) return
    let cancelled = false
    fetchMembers({ cluster_id, page: membersPage, page_size: 20, date_from: dateFrom, date_to: dateTo, subreddits, source })
      .then((m) => {
        if (!cancelled) setMembers(m)
      })
      .catch((err) => {
        if (!cancelled) setError(String(err))
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [membersPage, cluster_id, dateFrom, dateTo, subredditsKey, source])

  return {
    detail,
    velocity,
    members,
    membersPage,
    setMembersPage,
    corpusVelocity,
    loading,
    error,
  }
}
