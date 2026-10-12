import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDebouncedValue } from './useDebouncedValue'

describe('useDebouncedValue', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('updates after the configured delay', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, 450),
      { initialProps: { value: '' } },
    )

    expect(result.current).toBe('')
    rerender({ value: 'crm' })
    expect(result.current).toBe('')

    act(() => {
      vi.advanceTimersByTime(449)
    })
    expect(result.current).toBe('')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current).toBe('crm')
  })

  it('cancels pending updates when the value changes again', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, 450),
      { initialProps: { value: '' } },
    )

    rerender({ value: 'c' })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    rerender({ value: 'crm' })
    act(() => {
      vi.advanceTimersByTime(449)
    })
    expect(result.current).toBe('')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current).toBe('crm')
  })
})
