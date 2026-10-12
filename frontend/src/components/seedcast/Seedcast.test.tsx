import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildFixtureClusters, buildFixtureDetail, buildFixtureSnapshot } from '../../lib/seedcast/fixtures'

// Pages fetch through the API client; serve fixtures instead of calling /api/public.
vi.mock('../../api/seedcast', () => ({
  getSeedcastSnapshot: async () => buildFixtureSnapshot(),
  getSeedcastClusterDetail: async (id: number) => {
    const c = buildFixtureClusters().find((x) => x.id === id)
    return c ? buildFixtureDetail(c) : null
  },
}))
import ClusterRowList from './ClusterRowList'
import BubbleLandscape from './BubbleLandscape'
import MoversPage from './MoversPage'

afterEach(cleanup)

describe('BubbleLandscape', () => {
  const clusters = buildFixtureClusters()

  it('shows the largest cluster by default and switches color mode', () => {
    render(<BubbleLandscape clusters={clusters} />)
    const biggest = clusters.slice().sort((a, b) => b.posts4w - a.posts4w)[0]
    expect(screen.getAllByText(biggest.name).length).toBeGreaterThan(0)
    expect(screen.getByText('Rising')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Audience' }))
    expect(screen.getByText('B2B')).toBeTruthy()
  })

  it('updates the caption when a bubble is hovered', () => {
    render(<BubbleLandscape clusters={clusters} />)
    const target = clusters.slice().sort((a, b) => b.posts4w - a.posts4w)[5]
    fireEvent.mouseEnter(screen.getByLabelText(new RegExp(`^${target.name},`)))
    expect(screen.getByText(target.statement)).toBeTruthy()
  })
})

describe('ClusterRowList', () => {
  it('reveals more rows on demand', () => {
    const clusters = buildFixtureClusters()
    render(<ClusterRowList clusters={clusters} step={5} badge={() => ({ text: 'x', color: '#000' })} />)
    expect(screen.getAllByRole('link')).toHaveLength(5)
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }))
    expect(screen.getAllByRole('link')).toHaveLength(10)
  })
})

describe('MoversPage', () => {
  it('renders ten cards then a list, and switches tabs', async () => {
    render(<MoversPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fading' })).toBeTruthy())
    await waitFor(() => expect(document.querySelectorAll('a[href^="#/cluster/"]').length).toBeGreaterThan(10))
    fireEvent.click(screen.getByRole('button', { name: 'Bursting' }))
    await waitFor(() => expect(screen.getAllByText(/× burst/).length).toBeGreaterThan(0))
  })
})
