import createClient from 'openapi-fetch'
import type { components, paths } from './schema'
import { session } from './session'

type TokenOut = components['schemas']['TokenOut']

const REFRESH_PATH = '/api/v1/auth/refresh'
/** Requests where a 401 is an answer, not an expired access token: never refresh and retry. */
const NO_REFRESH =
  /^\/api\/v1\/(auth\/(login|refresh|password\/(forgot|reset|recover)|invitations\/)|registrations)/

let inflight: Promise<TokenOut | null> | null = null

async function requestRefresh(): Promise<TokenOut | null> {
  const response = await globalThis.fetch(REFRESH_PATH, {
    method: 'POST',
    credentials: 'same-origin',
  })
  if (!response.ok) return null
  return (await response.json()) as TokenOut
}

/**
 * Exchanges the refresh cookie for a new access token. Single flight: the refresh token rotates
 * on every use and a replayed one revokes the session, so concurrent callers (React StrictMode,
 * parallel 401s) share one request; Web Locks serialise it across tabs of the same browser.
 */
export function refreshSession(): Promise<TokenOut | null> {
  if (inflight) return inflight
  const run = async (): Promise<TokenOut | null> => {
    try {
      const out = await requestRefresh()
      if (out) session.set(out.access_token)
      else session.notifyLost()
      return out
    } catch {
      // Network trouble is not a refusal: keep the session and let the caller retry later.
      return null
    }
  }
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks
  const guarded = (locks ? locks.request('tarn-refresh', run) : run()) as Promise<TokenOut | null>
  const shared = guarded.finally(() => {
    inflight = null
  })
  inflight = shared
  return shared
}

function withToken(request: Request, token: string | null): Request {
  if (!token) return request
  const next = new Request(request)
  next.headers.set('Authorization', `Bearer ${token}`)
  return next
}

/** Adds the bearer token; on 401 refreshes once and repeats the request. */
export async function authFetch(request: Request): Promise<Response> {
  const retry = request.clone()
  const first = await globalThis.fetch(withToken(request, session.token))
  if (first.status !== 401 || NO_REFRESH.test(new URL(request.url).pathname)) return first
  const refreshed = await refreshSession()
  if (!refreshed) return first
  return globalThis.fetch(withToken(retry, refreshed.access_token))
}

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: authFetch,
})
