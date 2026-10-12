// Public (Seedcast) data contract. Mirrors fields already exposed by the
// dashboard API; the public endpoints that return it are specified in
// docs/reddit_analysis/05_potential_implementations/seedcast_public_frontend.md.

export type Audience = 'B2B' | 'B2C' | 'Mixed' | 'Unclear'

export interface SeedcastCluster {
  id: number
  name: string
  audience: Audience
  /** One representative pain statement from a member post. */
  statement: string
  /** ICP hint from the audience classifier, e.g. "freelancers". */
  icp: string | null
  posts4w: number
  total: number
  /** Last 4 weeks vs prior 4 weeks. null when either window has < 3 posts. */
  ratio: number | null
  /** 3-day burst lift vs prior 28 days. null when unavailable. */
  burst: number | null
  bursting: boolean
  communities: number
  topCommunity: string
  /** Display month, e.g. "Mar '26". */
  firstSeen: string
  workaroundPct: number
  /** Weekly post counts, oldest first. The last entry is the partial current week. */
  series: number[]
}

export interface SeedcastQuote {
  text: string
  source: string
  where: string
  date: string
  url: string | null
}

export interface SeedcastClusterDetail extends SeedcastCluster {
  /** Longer weekly history (about one year), oldest first, last entry partial. */
  history: number[]
  /** Share of all pain posts per week, in percent, aligned with history. */
  share: number[]
  quotes: SeedcastQuote[]
  where: Array<{ name: string; pct: number }>
  sources: Array<{ name: string; pct: number }>
  tools: Array<{ name: string; n: number }>
}

export interface SeedcastSnapshot {
  updatedAt: string | null
  clusters: SeedcastCluster[]
}

export type MoverMode = 'up' | 'down' | 'burst'
