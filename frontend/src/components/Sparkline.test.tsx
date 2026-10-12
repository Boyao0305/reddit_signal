import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import Sparkline from './Sparkline'

describe('Sparkline', () => {
  it('renders a dashed polyline for the partial-week segment', () => {
    const data = Array.from({ length: 15 }, (_, i) => i + 1)
    const { container } = render(
      <Sparkline data={data} partialFromIndex={14} />,
    )
    const dashed = container.querySelectorAll('polyline[stroke-dasharray]')
    expect(dashed.length).toBeGreaterThanOrEqual(1)
    expect(dashed[0].getAttribute('stroke-dasharray')).toBe('3,2')
  })

  it('renders solid polyline for completed weeks', () => {
    const data = Array.from({ length: 15 }, (_, i) => i + 1)
    const { container } = render(
      <Sparkline data={data} partialFromIndex={14} />,
    )
    const allPolylines = container.querySelectorAll('polyline')
    const solid = Array.from(allPolylines).filter(
      (p) => !p.hasAttribute('stroke-dasharray'),
    )
    expect(solid.length).toBeGreaterThanOrEqual(1)
  })

  it('renders a partial-week boundary marker even when the current week is null', () => {
    // Reproduces the live-data case: 14 historical weeks + null current week.
    const data: Array<number | null> = [
      12, 29, 35, 29, 67, 91, 120, 112, 121, 50, 43, 47, 41, 11, null,
    ]
    const { container } = render(
      <Sparkline data={data} partialFromIndex={14} />,
    )
    // The vertical boundary line is always present.
    const lines = container.querySelectorAll('line[stroke-dasharray]')
    expect(lines.length).toBeGreaterThanOrEqual(1)
  })
})
