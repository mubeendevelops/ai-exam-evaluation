import { useEffect, useState } from 'react'

interface Health {
  status: string
  version: string
  environment: string
  device: { kind: string; name: string; detail: string }
}

type ApiState = { phase: 'loading' } | { phase: 'ok'; health: Health } | { phase: 'down' }

export default function App() {
  const [api, setApi] = useState<ApiState>({ phase: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/v1/health', { signal: controller.signal })
      .then((res) => (res.ok ? (res.json() as Promise<Health>) : Promise.reject(new Error())))
      .then((health) => setApi({ phase: 'ok', health }))
      .catch(() => {
        if (!controller.signal.aborted) setApi({ phase: 'down' })
      })
    return () => controller.abort()
  }, [])

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-6 p-8">
      <header>
        <h1 className="text-3xl font-bold text-slate-900">Tarn AI Evaluation</h1>
        <p className="mt-1 text-slate-600">Development environment placeholder.</p>
      </header>
      <section aria-label="API status" className="rounded-lg border border-slate-200 p-4">
        {api.phase === 'loading' && <p className="text-slate-500">Checking API…</p>}
        {api.phase === 'down' && (
          <p className="text-red-700">
            <i className="fa-solid fa-circle-xmark mr-2" aria-hidden="true" />
            API unreachable. Run <code className="font-mono">make up</code>.
          </p>
        )}
        {api.phase === 'ok' && (
          <p className="text-emerald-700">
            <i className="fa-solid fa-circle-check mr-2" aria-hidden="true" />
            API {api.health.status} · v{api.health.version} · {api.health.environment} ·{' '}
            {api.health.device.kind.toUpperCase()}
          </p>
        )}
      </section>
    </main>
  )
}
