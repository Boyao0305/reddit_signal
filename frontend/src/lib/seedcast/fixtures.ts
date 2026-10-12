import type { Audience, SeedcastCluster, SeedcastClusterDetail, SeedcastSnapshot } from './types'

// Deterministic sample data in the public data contract. Used until the
// public endpoints exist (see api/seedcast.ts) and by tests.

const A = ['Invoice', 'Subscription', 'Onboarding', 'Cloud bill', 'Meeting', 'Hiring', 'Test flake', 'Inbox', 'Tax', 'Rent', 'Refund', 'Burnout']
const B = ['chasing', 'creep', 'overload', 'confusion', 'fatigue', 'delays', 'churn', 'surprises', 'lock-in', 'handoffs', 'duplication', 'drift']
const AUD: Audience[] = ['B2C', 'B2B', 'B2B', 'B2C', 'Mixed', 'Unclear']
const COMMUNITIES = ['r/smallbusiness', 'r/freelance', 'r/personalfinance', 'r/sysadmin', 'r/startups', 'r/Entrepreneur', 'r/webdev', 'r/accounting', 'r/productivity', 'r/devops', 'r/ecommerce', 'r/Teachers']
const MONTHS = ['Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep']
const ICPS = ['solo founders', 'small-team managers', 'freelancers', 'households', 'IT admins', 'indie developers']
const TOOLS = ['Excel', 'Notion', 'Stripe', 'QuickBooks', 'Zapier', 'Sheets', 'Airtable', 'Slack']

function rng(seed: number): () => number {
  let s = seed
  return () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296
}

export const FIXTURE_UPDATED_AT = '2026-10-05'
const COUNT = 144

export function buildFixtureClusters(count: number = COUNT): SeedcastCluster[] {
  const r = rng(7)
  const r2 = rng(99)
  const out: SeedcastCluster[] = []
  for (let i = 0; i < count; i++) {
    const w = Math.max(6, Math.round(420 * Math.pow(i + 1, -0.85) * (0.8 + 0.4 * r())))
    const rawRatio = Math.exp((r() - 0.45) * 1.8)
    const ratio = w < 12 ? null : rawRatio
    const burst = w < 12 ? null : Math.exp((r() - 0.3) * 2.2)
    const g = ratio ?? 1
    const series: number[] = []
    for (let t = 0; t < 26; t++) {
      const spike = r2() < 0.08 ? 1.35 : 1
      series.push((w / 4) * Math.pow(g, (t - 21.5) / 8) * (0.85 + 0.3 * r2()) * spike)
    }
    const name = `${A[i % 12]} ${B[Math.floor(i / 12)]}`
    out.push({
      id: i + 1,
      name,
      audience: AUD[Math.floor(r() * AUD.length)],
      statement: `People describe ongoing trouble with ${name.toLowerCase()} and the workarounds they rely on.`,
      icp: ICPS[Math.floor(r2() * ICPS.length)],
      posts4w: w,
      total: Math.round(w * (2.4 + r2() * 1.6)),
      ratio,
      burst,
      bursting: burst != null && burst > 2.2,
      communities: Math.round(4 + Math.sqrt(w) * 1.4),
      topCommunity: COMMUNITIES[Math.floor(r2() * COMMUNITIES.length)],
      firstSeen: `${MONTHS[Math.floor(r2() * 12)]} '${r2() < 0.5 ? 25 : 26}`,
      workaroundPct: Math.round(10 + r2() * 35),
      series,
    })
  }
  return out
}

export function buildFixtureSnapshot(): SeedcastSnapshot {
  return { updatedAt: FIXTURE_UPDATED_AT, clusters: buildFixtureClusters() }
}

export function buildFixtureDetail(c: SeedcastCluster): SeedcastClusterDetail {
  const r = rng(11 + c.id)
  const N = 52
  const history: number[] = []
  const lift = c.ratio ?? 1
  for (let i = 0; i < N; i++) {
    const base = (c.posts4w / 4) * Math.pow(lift, (i - (N - 4)) / 14)
    history.push(Math.max(1, base * (0.8 + 0.4 * r())))
  }
  history[N - 1] = history[N - 2] * 0.45
  const share = history.map((v, i) => (v / (900 + i * 12 + (r() - 0.5) * 80)) * 100)
  return {
    ...c,
    history,
    share,
    quotes: [
      { text: `[Representative pain statement about ${c.name.toLowerCase()}.]`, source: 'Reddit', where: c.topCommunity, date: '[date]', url: null },
      { text: `[Representative pain statement about ${c.name.toLowerCase()}.]`, source: 'Hacker News', where: 'news.ycombinator.com', date: '[date]', url: null },
      { text: `[Representative pain statement about ${c.name.toLowerCase()}.]`, source: 'X', where: '[community]', date: '[date]', url: null },
    ],
    where: [
      { name: c.topCommunity, pct: 34 },
      { name: COMMUNITIES[(c.id + 3) % COMMUNITIES.length], pct: 22 },
      { name: COMMUNITIES[(c.id + 5) % COMMUNITIES.length], pct: 15 },
      { name: COMMUNITIES[(c.id + 7) % COMMUNITIES.length], pct: 11 },
      { name: 'Hacker News', pct: 8 },
    ],
    sources: [
      { name: 'Reddit', pct: 62 },
      { name: 'Hacker News', pct: 21 },
      { name: 'X', pct: 11 },
      { name: 'YouTube', pct: 6 },
    ],
    tools: TOOLS.map((name, i) => ({ name, n: 41 - i * 5 })),
  }
}
