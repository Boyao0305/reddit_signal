import type { ReactNode } from 'react'

const TONES = {
  audience: 'bg-seed-tint text-seed-b2b',
  burst: 'bg-[#FDE7D0] text-[#8A3B00]',
  fade: 'bg-[#FDE7D0] text-[#8A3B00]',
  rise: 'bg-seed-tint text-seed-b2b',
} as const

export default function Pill({ tone = 'audience', size = 'md', children }: { tone?: keyof typeof TONES; size?: 'sm' | 'md'; children: ReactNode }) {
  const pad = size === 'sm' ? 'px-2.5 py-[3px] text-xs' : 'px-3 py-1 text-[13px]'
  return <span className={`inline-block rounded-full font-semibold ${pad} ${TONES[tone]}`}>{children}</span>
}
