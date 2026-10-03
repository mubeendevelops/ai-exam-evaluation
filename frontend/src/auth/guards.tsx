import { Navigate, Outlet, useLocation } from 'react-router'
import { useAuth } from './AuthProvider'

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

/** Administrator pages; teachers are sent back to the Q&A tab. */
export function RequireAdmin() {
  const { state } = useAuth()
  if (state.status === 'authenticated' && state.me.user.role !== 'admin') {
    return <Navigate to="/qna" replace />
  }
  return <Outlet />
}

/**
 * Landing and sign-in pages: a visitor who is already signed in goes straight to the app. They
 * render while the refresh cookie is checked, so a first-time visitor never waits on it.
 */
export function RedirectIfSignedIn() {
  const { state } = useAuth()
  if (state.status === 'authenticated') return <Navigate to="/qna" replace />
  return <Outlet />
}
