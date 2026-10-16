import type { ReactNode } from 'react'

export type SeedcastNav = 'landscape' | 'movers' | 'compare' | 'about'

const LIFT = {
  md: { band: 'pb-[106px]', body: '-mt-[90px]' },
  lg: { band: 'pb-[120px]', body: '-mt-[92px]' },
  sm: { band: 'pb-24', body: '-mt-16' },
} as const

const NAV: Array<{ key: SeedcastNav; label: string; href: string }> = [
  { key: 'landscape', label: 'Landscape', href: '#/' },
  { key: 'movers', label: 'Movers', href: '#/movers' },
  { key: 'compare', label: 'Compare', href: '#/compare' },
  { key: 'about', label: 'About', href: '#/about' },
]

interface Props {
  active: SeedcastNav | null
  title: ReactNode
  /** Small element aligned right of the title (e.g. tags, update date). */
  aside?: ReactNode
  /** Optional line above the title (e.g. a back link). */
  eyebrow?: ReactNode
  /** Optional short line under the title. */
  intro?: ReactNode
  lift?: keyof typeof LIFT
  footerNote?: string
  children: ReactNode
}

export default function SiteShell({ active, title, aside, eyebrow, intro, lift = 'md', footerNote, children }: Props) {
  const l = LIFT[lift]
  return (
    <div className="min-h-screen bg-seed-bg font-seed text-seed-ink text-base antialiased">
      <div className={`bg-seed-band text-white ${l.band}`}>
        <div className="mx-auto max-w-[1240px] px-6">
          <header className="flex flex-wrap items-center justify-between gap-3 py-6">
            <a href="#/" className="flex items-center gap-2.5 text-[19px] font-bold text-white no-underline">
              <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
                <circle cx="10" cy="17" r="8" fill="#B58CF0" />
                <circle cx="21" cy="8" r="5.5" fill="#F08A24" />
              </svg>
              Seedcast
            </a>
            <nav aria-label="Main" className="flex flex-wrap gap-2 text-[15px] font-medium">
              {NAV.map((n) => (
                <a
                  key={n.key}
                  href={n.href}
                  aria-current={active === n.key ? 'page' : undefined}
                  className={`rounded-full px-4 py-3 text-white no-underline ${active === n.key ? 'bg-white/15' : 'opacity-80 hover:opacity-100'}`}
                >
                  {n.label}
                </a>
              ))}
              <a
                href="https://github.com/Boyao0305/reddit_signal"
                className="px-4 py-3 text-white no-underline opacity-80 hover:opacity-100"
              >
                GitHub
              </a>
            </nav>
          </header>
          {eyebrow}
          <div className="mt-10 flex flex-wrap items-end justify-between gap-5">
            <h1 className="m-0 max-w-[820px] text-[40px] font-extrabold leading-[1.04] tracking-[-0.03em] sm:text-[56px] lg:text-[60px]">
              {title}
            </h1>
            {aside}
          </div>
          {intro && <p className="mb-0 mt-4 max-w-[760px] text-[17px] leading-[1.5] text-[#D9C8F5]">{intro}</p>}
        </div>
      </div>
      <div className={`mx-auto max-w-[1240px] px-6 pb-14 ${l.body}`}>
        {children}
        <footer className="flex flex-wrap justify-between gap-x-6 gap-y-2 px-1 pt-7 text-[13px] text-seed-muted">
          <span>Sources: Reddit · Hacker News</span>
          <span>{footerNote ?? 'Open source · No paywall'}</span>
        </footer>
      </div>
    </div>
  )
}
