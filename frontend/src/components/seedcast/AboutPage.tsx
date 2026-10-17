import SiteShell from './SiteShell'

const PEOPLE = [
  {
    name: 'Boyao Xu',
    links: [
      { label: 'LinkedIn', href: 'https://www.linkedin.com/in/boyaoxu' },
      { label: 'GitHub', href: 'https://github.com/Boyao0305' },
      { label: 'benchology.co', href: 'https://benchology.co' },
    ],
  },
  {
    name: 'Linzzi Ji',
    links: [
      { label: 'LinkedIn', href: 'https://www.linkedin.com/in/linzzi-ji-2627b032a/' },
      { label: 'GitHub', href: 'https://github.com/yzz0806' },
    ],
  },
]

export default function AboutPage() {
  return (
    <SiteShell active="about" title="About Seedcast" lift="sm">
      <section className="rounded-[18px] border border-seed-line bg-white px-6 py-8 text-[17px] leading-[1.65] text-seed-ink2 shadow-[0_16px_44px_rgba(58,22,112,0.16)] sm:px-10">
        <div className="max-w-[720px] space-y-5">
          <p className="m-0">
            Seedcast reads Reddit and Hacker News every day and pulls out the problems people describe. An LLM extracts
            each complaint with a short supporting quote. Similar complaints are grouped into clusters, and each cluster
            is tracked week by week.
          </p>
          <p className="m-0">
            I built it between April and July 2026. The plan was to find high-pain, low-competition problems, use AI to
            build apps against them quickly, and use how those apps did to validate and calibrate the predictions.
            Building turned out to be cheap. Getting each app in front of users cost too much, so I stopped building apps
            in July.
          </p>
          <p className="m-0">
            The pipeline still runs, and this site is its public view. Usernames are never shown, and quotes are short
            excerpts. The code is{' '}
            <a href="https://github.com/Boyao0305/reddit_signal" className="font-semibold text-seed-accent">
              open source
            </a>{' '}
            (MIT).
          </p>
          <div className="space-y-2 border-t border-seed-line pt-5 text-seed-ink">
            {PEOPLE.map((person) => (
              <p key={person.name} className="m-0">
                <span className="font-bold">{person.name}</span>
                {person.links.map((l) => (
                  <span key={l.href}>
                    {' · '}
                    <a href={l.href} className="font-semibold text-seed-accent">
                      {l.label}
                    </a>
                  </span>
                ))}
              </p>
            ))}
          </div>
        </div>
      </section>
    </SiteShell>
  )
}
