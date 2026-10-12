import type { RunsResponse, ScoreConfig } from '../types'
import { getCachedJSON } from './client'

const RUNS_CACHE_TTL_MS = 30_000

export function fetchRuns(): Promise<RunsResponse> {
  return getCachedJSON<RunsResponse>('/api/v2/runs', RUNS_CACHE_TTL_MS)
}

export function fetchScoreConfig(): Promise<ScoreConfig> {
  return getCachedJSON<ScoreConfig>('/api/v2/score-config')
}

