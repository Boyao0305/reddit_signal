import { describe, expect, it } from 'vitest'
import { buildFixtureClusters, buildFixtureDetail } from './fixtures'
import { bubbleLabel, formatDate, formatRatio, keywordOf, momentumOf, twoLines } from './format'
import { AUDIENCE_FILL, SEED, wordOn } from './theme'
import { packCircles, packFixed } from './pack'
import { selectBubbles } from './bubbles'
import { areaPath, linePath, scaleSeries } from './paths'
import { rankMovers } from './rank'
import { parseRoute } from './route'

describe('packCircles', () => {
  const radii = buildFixtureClusters()
    .map((c) => Math.sqrt(c.posts4w) * 3)
    .sort((a, b) => b - a)
  const packed = packCircles(radii, { width: 1100, height: 600 })

  it('returns one circle per input, in order', () => {
    expect(packed).toHaveLength(radii.length)
    expect(packed[0].r).toBeGreaterThan(packed[packed.length - 1].r)
  })

  it('never overlaps and stays inside the canvas', () => {
    for (let i = 0; i < packed.length; i++) {
      const a = packed[i]
      expect(a.x - a.r).toBeGreaterThanOrEqual(-0.5)
      expect(a.x + a.r).toBeLessThanOrEqual(1100.5)
      expect(a.y - a.r).toBeGreaterThanOrEqual(-0.5)
      expect(a.y + a.r).toBeLessThanOrEqual(600.5)
      for (let j = i + 1; j < packed.length; j++) {
        const b = packed[j]
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(a.r + b.r - 0.01)
      }
    }
  })

  it('handles empty input', () => {
    expect(packCircles([], { width: 10, height: 10 })).toEqual([])
  })
})

describe('rankMovers', () => {
  const clusters = buildFixtureClusters()

  it('orders rising by ratio descending and respects the minimum volume', () => {
    const up = rankMovers(clusters, 'up')
    expect(up.every((c) => c.posts4w >= 20)).toBe(true)
    for (let i = 1; i < up.length; i++) expect(up[i - 1].ratio!).toBeGreaterThanOrEqual(up[i].ratio!)
  })

  it('orders fading ascending and bursting by burst score', () => {
    const down = rankMovers(clusters, 'down')
    for (let i = 1; i < down.length; i++) expect(down[i - 1].ratio!).toBeLessThanOrEqual(down[i].ratio!)
    const burst = rankMovers(clusters, 'burst')
    for (let i = 1; i < burst.length; i++) expect(burst[i - 1].burst!).toBeGreaterThanOrEqual(burst[i].burst!)
  })

  it('drops clusters without a ratio', () => {
    const c = { ...clusters[0], ratio: null }
    expect(rankMovers([c], 'up', 0)).toEqual([])
  })
})

describe('paths', () => {
  it('scales a series into the box', () => {
    const pts = scaleSeries([0, 5, 10], 100, 50, { top: 0, bottom: 0 })
    expect(pts[0]).toEqual([0, 50])
    expect(pts[2]).toEqual([100, 0])
  })

  it('builds open and closed paths', () => {
    expect(linePath([1, 2], 10, 10)).toMatch(/^M0\.0 .* L10\.0 /)
    expect(areaPath([1, 2], 10, 10)).toMatch(/Z$/)
    expect(areaPath([], 10, 10)).toBe('')
  })
})

describe('format and route', () => {
  it('classifies momentum with null as flat', () => {
    expect(momentumOf(null)).toBe('flat')
    expect(momentumOf(1.25)).toBe('rising')
    expect(momentumOf(0.8)).toBe('fading')
    expect(momentumOf(1)).toBe('flat')
    expect(formatRatio(null)).toBe('—')
    expect(formatRatio(2)).toBe('▲ 2.00×')
  })

  it('splits labels into two balanced lines', () => {
    expect(twoLines('Cloud bill surprises')).toEqual(['Cloud bill', 'surprises'])
    expect(twoLines('Tax')).toEqual(['Tax', ''])
  })

  it('parses hash routes', () => {
    expect(parseRoute('')).toEqual({ name: 'landscape' })
    expect(parseRoute('#/movers')).toEqual({ name: 'movers' })
    expect(parseRoute('#/compare/')).toEqual({ name: 'compare' })
    expect(parseRoute('#/about')).toEqual({ name: 'about' })
    expect(parseRoute('#/cluster/12')).toEqual({ name: 'cluster', id: 12 })
    expect(parseRoute('#/cluster/x')).toEqual({ name: 'landscape' })
  })

  it('builds a detail with aligned history and share', () => {
    const d = buildFixtureDetail(buildFixtureClusters()[0])
    expect(d.history).toHaveLength(52)
    expect(d.share).toHaveLength(52)
    expect(d.sources.reduce((a, s) => a + s.pct, 0)).toBe(100)
  })
})

describe('bubble keywords', () => {
  it('takes the topic from each cluster-name shape, in sentence case', () => {
    expect(keywordOf('[AI Assistant]: Unreliable, inconsistent performance.')).toBe('AI assistant')
    expect(keywordOf('[Productivity: Information overload and tab chaos]')).toBe('Productivity')
    expect(keywordOf('Self-improvement: Users struggle with motivation.')).toBe('Self-improvement')
    expect(keywordOf('[YouTube Experience: algorithmic control]')).toBe('YouTube experience')
    expect(keywordOf('[Work/Life Balance]: Commutes erode personal time.')).toBe('Work/Life balance') // first word as written
  })

  it('gives no keyword for an unlabeled cluster (joined member statements)', () => {
    expect(keywordOf('Home Assistant was too much work to set up. | Need a simple way to trigger it')).toBe('')
  })

  it('shortens a topic that is really a sentence', () => {
    expect(keywordOf('[Users cannot reconcile invoices across many tools and lose hours]')).toBe('Users cannot reconcile')
  })

  it('grows the font with the bubble and wraps when one line does not fit', () => {
    const big = bubbleLabel('Invoice chasing', 80)!
    const small = bubbleLabel('Invoice chasing', 34)!
    expect(big.fontSize).toBeGreaterThan(small.fontSize)
    expect(small.lines).toEqual(['Invoice', 'chasing'])
    expect(bubbleLabel('Onboarding', 60)!.lines).toEqual(['Onboarding'])
    expect(bubbleLabel('Parenting/Sleep', 36)!.lines).toEqual(['Parenting/', 'Sleep'])
    expect(small.dy[0]).toBeLessThan(0)
    expect(small.dy[1]).toBeGreaterThan(0)
  })

  it('leaves tiny bubbles unlabeled', () => {
    expect(bubbleLabel('Subscription management', 12)).toBeNull()
    expect(bubbleLabel('', 80)).toBeNull()
  })

  it('colors words per the style rules', () => {
    expect(wordOn(SEED.accent)).toBe('#FFC46B')
    expect(wordOn(SEED.fade)).toBe('#3A1670')
    expect(wordOn(SEED.steady)).toBe('#632CA6')
    expect(wordOn(AUDIENCE_FILL.B2B)).toBe('#F6B96B')
    expect(wordOn(AUDIENCE_FILL.B2C)).toBe('#2A0F52')
    expect(wordOn(AUDIENCE_FILL.Mixed)).toBe('#3A1670')
  })
})

describe('bubble selection and fixed-scale packing', () => {
  const cluster = (id: number, posts4w: number) => ({ ...buildFixtureClusters()[0], id, posts4w })

  const opts = { maxRadius: 100, minRadius: 9, featured: 2, medium: 3, mediumUntilRank: 10, small: 4 }
  const ranked = (n: number, top: number) => Array.from({ length: n }, (_, i) => cluster(i + 1, Math.max(1, Math.round(top / (i + 1)))))

  it('gives the biggest cluster the max radius whatever its count', () => {
    for (const top of [20, 2000]) {
      const chosen = selectBubbles([cluster(1, top), cluster(2, top / 4)], opts)
      expect(chosen[0].r).toBe(100)
      expect(chosen[1].r).toBeCloseTo(50)
    }
  })

  it('samples every size tier, not only the biggest', () => {
    const chosen = selectBubbles(ranked(400, 4000), opts)
    const ids = chosen.map((b) => b.cluster.id)
    expect(ids.slice(0, 2)).toEqual([1, 2])                                  // featured
    expect(ids.slice(2, 5).every((id) => id > 2 && id <= 10)).toBe(true)     // medium, ranks 3..10
    expect(ids.slice(5).every((id) => id > 10)).toBe(true)                   // small, the rest
    expect(chosen).toHaveLength(9)
    expect(chosen[chosen.length - 1].r).toBeGreaterThanOrEqual(9)                       // never below minRadius
  })

  it('is deterministic and skips clusters without recent posts', () => {
    const data = [...ranked(50, 500), cluster(999, 0)]
    expect(selectBubbles(data, opts)).toEqual(selectBubbles(data.slice().reverse(), opts))
    expect(selectBubbles(data, opts).some((b) => b.cluster.id === 999)).toBe(false)
  })

  it('puts the post count under the keyword when there is room', () => {
    const big = bubbleLabel('Invoice chasing', 80, '212')!
    expect(big.sub?.text).toBe('212')
    expect(big.sub!.dy).toBeGreaterThan(Math.max(...big.dy))
    const tight = bubbleLabel('Invoice chasing', 34, '1,234,567 posts')!   // too wide: keyword only
    expect(tight.lines).toEqual(['Invoice', 'chasing'])
    expect(tight.sub).toBeUndefined()
  })

  it('packs at the given scale, inside the canvas, leaving out what does not fit', () => {
    const spots = packFixed([200, 150, 150, 150, 150, 150], { width: 1100, height: 600 })
    expect(spots[0]).toMatchObject({ x: 550, y: 300, r: 200 })
    const placed = spots.filter((p): p is NonNullable<typeof p> => p !== null)
    expect(placed.length).toBeLessThan(spots.length)
    for (const p of placed) {
      expect(p.x - p.r).toBeGreaterThanOrEqual(8)
      expect(p.y + p.r).toBeLessThanOrEqual(592)
    }
  })

  it('formats full timestamps as dates', () => {
    expect(formatDate('2026-10-15T13:40:27.281984+00:00')).toBe('Oct 15, 2026')
  })
})
