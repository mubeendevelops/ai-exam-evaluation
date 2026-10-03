import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'

interface TokenState {
  token?: string
  forced?: boolean
}

/**
 * The single-use token of an emailed link (`?token=…`) or of a forced password reset (router
 * state). It is kept in component state and removed from the address bar and history at once, so
 * it does not linger in the browser history or get copied by mistake.
 */
export function useLinkToken(): { token: string | null; forced: boolean } {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const state = (location.state ?? {}) as TokenState
  const fromUrl = params.get('token')
  const [token] = useState<string | null>(() => fromUrl ?? state.token ?? null)

  useEffect(() => {
    if (fromUrl) navigate(location.pathname, { replace: true, state: { token: fromUrl } })
  }, [fromUrl, location.pathname, navigate])

  return { token, forced: state.forced === true }
}
