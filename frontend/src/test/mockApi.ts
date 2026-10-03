import { vi } from 'vitest'

export interface Call {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
  headers: Headers
}

export interface Reply {
  status?: number
  json?: unknown
  body?: BodyInit
  headers?: Record<string, string>
}

type Handler = Reply | ((call: Call) => Reply | Promise<Reply>)

/**
 * Stubs `fetch` with canned answers keyed `"METHOD /path"`. An unmatched request fails the test
 * loudly (status 599 and an entry in `unhandled`) instead of silently hitting the network.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = []
  const unhandled: string[] = []

  const fake = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request =
      input instanceof Request
        ? input
        : new Request(new URL(String(input), 'http://localhost'), init)
    const url = new URL(request.url)
    const text = request.method === 'GET' ? '' : await request.clone().text()
    let body: unknown = text
    try {
      body = text ? JSON.parse(text) : undefined
    } catch {
      // not JSON (the roster CSV)
    }
    const call: Call = {
      method: request.method,
      path: url.pathname,
      query: url.searchParams,
      body,
      headers: request.headers,
    }
    calls.push(call)
    const key = `${call.method} ${call.path}`
    const route = routes[key]
    if (!route) {
      unhandled.push(key)
      return new Response(JSON.stringify({ detail: `unhandled ${key}` }), { status: 599 })
    }
    const reply = typeof route === 'function' ? await route(call) : route
    const status = reply.status ?? 200
    if (status === 204) return new Response(null, { status })
    if (reply.body !== undefined)
      return new Response(reply.body, { status, headers: reply.headers })
    return new Response(JSON.stringify(reply.json ?? {}), {
      status,
      headers: { 'Content-Type': 'application/json', ...reply.headers },
    })
  }

  const stub = vi.fn(fake)
  vi.stubGlobal('fetch', stub)
  return {
    calls,
    unhandled,
    callsTo: (key: string) => calls.filter((c) => `${c.method} ${c.path}` === key),
  }
}
