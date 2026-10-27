import { Navigate, Outlet, useLocation } from 'react-router'
import { useAuth } from './AuthProvider'
import { can, homeOf, type Capability } from './capabilities'

function Splash() {
  return (
    <div
      role="status"
      className="flex min-h-screen items-center justify-center bg-dark-bg text-sm text-gray-400"
    >
      <i className="fa-solid fa-circle-notch fa-spin mr-2 text-purple-400" aria-hidden="true" />
      Loading…
    </div>
  )
}

/** Signed-in pages. Visitors go to the sign-in card on `/`. */
export function RequireAuth() {
  const { state } = useAuth()
  const location = useLocation()
  if (state.status === 'loading') return <Splash />
  if (state.status === 'anonymous')
    return <Navigate to="/" replace state={{ from: location.pathname }} />
  return <Outlet />
}

/**
 * Pages for the personas that have `capability` (D177); anyone else goes to their own home.
 * The server checks the same table, so this only spares people a page of refusals.
 */
export function RequireCapability({ capability }: { capability: Capability }) {
  const { state } = useAuth()
  if (state.status === 'authenticated' && !can(state.me, capability)) {
    return <Navigate to={homeOf(state.me)} replace />
  }
  return <Outlet />
}

/** The Governor's page: the other personas go home. */
export function RequireGovernor() {
  const { state } = useAuth()
  if (state.status === 'authenticated' && state.me.user.role !== 'governor') {
    return <Navigate to={homeOf(state.me)} replace />
  }
  return <Outlet />
}

/**
 * Landing and sign-in pages: a visitor who is already signed in goes straight to the app. They
 * render while the refresh cookie is checked, so a first-time visitor never waits on it.
 */
export function RedirectIfSignedIn() {
  const { state } = useAuth()
  if (state.status === 'authenticated') return <Navigate to={homeOf(state.me)} replace />
  return <Outlet />
}
