import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => vi.unstubAllGlobals())

describe('App placeholder', () => {
  it('shows the API status once the health check succeeds', async () => {
    const health = {
      status: 'ok',
      version: '0.0.0',
      environment: 'test',
      device: { kind: 'cpu', name: 'cpu', detail: 'CPU selected' },
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(health) }),
    )
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Tarn AI Evaluation' })).toBeInTheDocument()
    expect(await screen.findByText(/API ok · v0\.0\.0 · test · CPU/)).toBeInTheDocument()
  })

  it('tells the developer when the API is down', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    render(<App />)
    expect(await screen.findByText(/API unreachable/)).toBeInTheDocument()
  })
})
