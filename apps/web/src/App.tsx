import { useEffect, useState } from 'react'

type Health = {
  status: string
  service: string
  version: string
  mode: string
}

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; health: Health }
  | { kind: 'failed'; message: string }

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export default function App() {
  const [state, setState] = useState<ViewState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${apiBaseUrl}/api/v1/health`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Health check failed with ${response.status}`)
        return (await response.json()) as Health
      })
      .then((health) => setState({ kind: 'ready', health }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState({ kind: 'failed', message: error instanceof Error ? error.message : 'Unknown error' })
      })
    return () => controller.abort()
  }, [])

  return (
    <main className="shell">
      <section className="panel" aria-live="polite">
        <p className="eyebrow">Stage 0 · Foundation</p>
        <h1>EviStream</h1>
        <p className="summary">Evidence-grounded investigation for long-form video moderation.</p>

        {state.kind === 'loading' && <p className="status neutral">Checking API health…</p>}
        {state.kind === 'failed' && (
          <div className="status failed">
            <strong>API unavailable</strong>
            <span>{state.message}</span>
          </div>
        )}
        {state.kind === 'ready' && (
          <div className="status ready">
            <div>
              <span className="dot" />
              <strong>API operational</strong>
            </div>
            <dl>
              <div><dt>Service</dt><dd>{state.health.service}</dd></div>
              <div><dt>Version</dt><dd>{state.health.version}</dd></div>
              <div><dt>Mode</dt><dd>{state.health.mode}</dd></div>
            </dl>
          </div>
        )}
      </section>
    </main>
  )
}
