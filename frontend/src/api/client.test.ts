import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '../test/mockApi'
import { tokenOf } from '../test/fixtures'
import { api, refreshSession } from './client'
import { session } from './session'

beforeEach(() => session.clear())
afterEach(() => vi.unstubAllGlobals())

describe('API client token handling', () => {
  it('sends the access token as a bearer header', async () => {
    const mock = mockApi({ 'GET /api/v1/auth/me': { json: {} } })
    session.set('tok-1')
    await api.GET('/api/v1/auth/me')
    expect(mock.calls[0]?.headers.get('Authorization')).toBe('Bearer tok-1')
  })

  it('refreshes once on a 401 and repeats the request with the new token', async () => {
    let first = true
    const mock = mockApi({
      'GET /api/v1/auth/me': (call) => {
        if (call.headers.get('Authorization') === 'Bearer fresh') return { json: { ok: true } }
        first = false
        return { status: 401, json: { detail: 'expired' } }
      },
      'POST /api/v1/auth/refresh': { json: tokenOf({}, 'fresh') },
    })
    session.set('stale')
    const { response } = await api.GET('/api/v1/auth/me')
    expect(first).toBe(false)
    expect(response.status).toBe(200)
    expect(session.token).toBe('fresh')
    expect(mock.callsTo('POST /api/v1/auth/refresh')).toHaveLength(1)
  })

  it('shares one refresh between parallel 401s (the refresh token rotates on use)', async () => {
    const mock = mockApi({
      'GET /api/v1/auth/me': (call) =>
        call.headers.get('Authorization') === 'Bearer fresh'
          ? { json: {} }
          : { status: 401, json: { detail: 'expired' } },
      'GET /api/v1/accounts': (call) =>
        call.headers.get('Authorization') === 'Bearer fresh'
          ? { json: [] }
          : { status: 401, json: { detail: 'expired' } },
      'POST /api/v1/auth/refresh': async () => {
        await new Promise((resolve) => setTimeout(resolve, 20))
        return { json: tokenOf({}, 'fresh') }
      },
    })
    session.set('stale')
    const [a, b] = await Promise.all([api.GET('/api/v1/auth/me'), api.GET('/api/v1/accounts')])
    expect(a.response.status).toBe(200)
    expect(b.response.status).toBe(200)
    expect(mock.callsTo('POST /api/v1/auth/refresh')).toHaveLength(1)
  })

  it('ends the session when the refresh is refused', async () => {
    mockApi({
      'GET /api/v1/auth/me': { status: 401, json: { detail: 'expired' } },
      'POST /api/v1/auth/refresh': { status: 401, json: { detail: 'Sign in again.' } },
    })
    const lost = vi.fn()
    const off = session.onLost(lost)
    session.set('stale')
    const { response } = await api.GET('/api/v1/auth/me')
    off()
    expect(response.status).toBe(401)
    expect(lost).toHaveBeenCalledOnce()
    expect(session.token).toBeNull()
  })

  it('does not refresh after a failed sign-in or recovery: a 401 there is the answer', async () => {
    const mock = mockApi({
      'POST /api/v1/auth/login': { status: 401, json: { detail: 'Sign-in failed.' } },
      'POST /api/v1/auth/password/recover': { status: 401, json: { detail: 'Recovery failed.' } },
    })
    session.set('tok')
    await api.POST('/api/v1/auth/login', {
      body: { institution_id: 'X', email: 'a@b.test', password: 'p', remember: false },
    })
    await api.POST('/api/v1/auth/password/recover', {
      body: { institution_id: 'X', email: 'a@b.test', recovery_code: 'c', new_password: 'p' },
    })
    expect(mock.callsTo('POST /api/v1/auth/refresh')).toHaveLength(0)
    expect(session.token).toBe('tok')
  })

  it('keeps the session when the network fails during a refresh', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    session.set('tok')
    expect(await refreshSession()).toBeNull()
    expect(session.token).toBe('tok')
  })
})
