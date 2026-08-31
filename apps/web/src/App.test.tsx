import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => vi.restoreAllMocks())

describe('App', () => {
  it('shows the healthy API response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', service: 'evistream-api', version: '0.1.0.dev0', mode: 'test' }),
    }))

    render(<App />)
    expect(screen.getByText('Checking API health…')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('API operational')).toBeInTheDocument())
    expect(screen.getByText('0.1.0.dev0')).toBeInTheDocument()
  })

  it('shows a useful failure state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('connection refused')))
    render(<App />)
    await waitFor(() => expect(screen.getByText('API unavailable')).toBeInTheDocument())
    expect(screen.getByText('connection refused')).toBeInTheDocument()
  })
})

