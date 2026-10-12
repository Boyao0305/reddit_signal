import { describe, expect, it } from 'vitest'
import { buildFixtureClusters, buildFixtureDetail } from './fixtures'
import { formatRatio, momentumOf, twoLines } from './format'
import { packCircles } from './pack'
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
