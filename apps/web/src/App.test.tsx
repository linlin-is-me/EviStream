import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => vi.restoreAllMocks())

describe('Stage 6 console', () => {
  it('loads model profiles and videos in the task center', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      return {
        ok: true,
        json: async () => url.includes('model-profiles')
          ? { items: [{ name: 'mock', gateway: 'mock', configured: true, models: {} }], next_cursor: null }
          : { items: [{ video_id: 'vid_1', original_name: 'sample.mp4', status: 'READY', triage_status: 'SUCCEEDED', model_profile: 'mock', duration_ms: 30000 }], next_cursor: null },
      }
    }))
    render(<App />)
    expect(screen.getByRole('heading', { name: '任务中心' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('sample.mp4')).toBeInTheDocument())
    expect(screen.getByRole('option', { name: 'mock · mock' })).toBeInTheDocument()
  })

  it('shows API errors without hiding the workspace', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ error_code: 'QUEUE_UNAVAILABLE', message: 'offline' }),
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('QUEUE_UNAVAILABLE'))
  })
})
