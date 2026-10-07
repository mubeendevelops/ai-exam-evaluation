import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import App from '../App'
import { AuthProvider } from '../auth/AuthProvider'
import { session } from '../api/session'
import { resetBloomCache } from '../hooks/usePasswordBloom'
import { healthy } from './fixtures'

export function resetClientState() {
  session.clear()
  resetBloomCache()
}

/** The whole app at `path`, with fresh query cache and auth state. */
export function renderApp(path = '/', state?: unknown) {
  resetClientState()
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[
          state === undefined
            ? path
            : {
                pathname: path.split('?')[0],
                search: path.includes('?') ? `?${path.split('?')[1]}` : '',
                state,
              },
        ]}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return Object.assign(rendered, { queryClient })
}

/** Routes every test needs when the visitor is not signed in. */
export const anonymous = {
  'POST /api/v1/auth/refresh': { status: 401, json: { detail: 'Sign in again.' } },
  'GET /api/v1/health': { json: healthy },
}
