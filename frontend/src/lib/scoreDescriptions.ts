import type { DimensionKey } from '../types'

// Plain-English one-liners for the WeightsPanel hover tooltips.
export const SCORE_DESCRIPTIONS: Record<DimensionKey, string> = {
  coherence:
    'How tightly the pain statements cluster semantically — high means members talk about the same thing.',
  thread_diversity:
    'Fraction of distinct source posts; low means an echo-chamber where one thread dominates.',
  author_diversity:
    'Fraction of distinct authors; low suggests a handful of vocal users rather than broad demand.',
  subreddit_count:
    'How many subreddits surface this pain — wider reach implies a less niche problem.',
  specificity_mean:
    'Average specificity of the underlying pain statements — concrete complaints score higher than vague ones.',
  workaround_ratio:
    'Share of members who mention hacking together a workaround — high means real, unmet need.',
  separation:
    'How distinct this cluster is from its neighbors in embedding space — high means a clean, well-defined topic.',
  temporal_spread_days:
    'Number of days the pain has been discussed — sustained complaints score higher than one-off spikes.',
  q_specificity:
    'LLM judgment of how specific (vs generic) the cluster is — actionable problems beat philosophical ones.',
  v_buildability:
    'LLM judgment of how feasible it is to ship a solution — favors clear, scoped problems.',
  v_icp_clarity:
    'LLM judgment of how clearly defined the customer is — niche-but-named beats vague-and-large.',
  v_moat_potential:
    'LLM judgment of defensibility — does solving this create a durable advantage?',
  slope_per_week:
    'Linear trend in weekly pain mentions — positive means the problem is growing.',
  velocity_4w_ratio_normalized:
    '4w/prior-4w acceleration after corpus normalization — unavailable only when either 4w bucket is too sparse.',
}
