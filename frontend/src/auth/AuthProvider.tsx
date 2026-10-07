import { useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { api, refreshSession } from '../api/client'
import { problemOf, NETWORK_PROBLEM } from '../api/errors'
import type { components } from '../api/schema'
import { session } from '../api/session'

type Me = components['schemas']['MeOut']

export type AuthState =
  { status: 'loading' } | { status: 'anonymous' } | { status: 'authenticated'; me: Me }

export type SignInResult =
  | { kind: 'signed_in' }
  | { kind: 'reset_required'; resetToken: string }
  | { kind: 'error'; message: string }

export interface SignInInput {
  institutionId: string
  email: string
  password: string
  remember: boolean
}

interface AuthApi {
  state: AuthState
  signIn: (input: SignInInput) => Promise<SignInResult>
  signOut: () => Promise<void>
  /** Reloads `/auth/me` (e.g. after new recovery codes changed the count). */
  reload: () => Promise<void>
}

const AuthContext = createContext<AuthApi | null>(null)

async function loadMe(): Promise<Me | null> {
  const { data } = await api.GET('/api/v1/auth/me')
  return data ?? null
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })
  const queryClient = useQueryClient()

  // On load: the refresh cookie, if any, gives an access token.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const refreshed = await refreshSession()
      const me = refreshed ? await loadMe() : null
      if (!cancelled) setState(me ? { status: 'authenticated', me } : { status: 'anonymous' })
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // A refused refresh anywhere (expired, revoked, replayed) ends the session in the UI. The
  // query cache (booklets, students, marks) goes with it: the next person to sign in on this
  // browser, perhaps of another college, must not see it, even for a moment.
  useEffect(
    () =>
      session.onLost(() => {
        queryClient.clear()
        setState({ status: 'anonymous' })
      }),
    [queryClient],
  )

  const signIn = useCallback(
    async (input: SignInInput): Promise<SignInResult> => {
      try {
        const { data, error } = await api.POST('/api/v1/auth/login', {
          body: {
            institution_id: input.institutionId.trim(),
            email: input.email.trim(),
            password: input.password,
            remember: input.remember,
          },
        })
        if (!data) {
          return {
            kind: 'error',
            message: problemOf(
              error,
              'Sign-in failed. Check the Institution ID, email and password.',
            ).message,
          }
        }
        if ('reset_token' in data) return { kind: 'reset_required', resetToken: data.reset_token }
        session.set(data.access_token)
        queryClient.clear()
        const me = await loadMe()
        if (!me) return { kind: 'error', message: 'Sign-in failed. Please try again.' }
        setState({ status: 'authenticated', me })
        return { kind: 'signed_in' }
      } catch {
        return { kind: 'error', message: NETWORK_PROBLEM }
      }
    },
    [queryClient],
  )

  const signOut = useCallback(async () => {
    try {
      await api.POST('/api/v1/auth/logout')
    } catch {
      // The server session may already be gone; the browser forgets it either way.
    }
    session.clear()
    queryClient.clear()
    setState({ status: 'anonymous' })
  }, [queryClient])

  const reload = useCallback(async () => {
    const me = await loadMe()
    if (me) setState({ status: 'authenticated', me })
  }, [])

  const value = useMemo(
    () => ({ state, signIn, signOut, reload }),
    [state, signIn, signOut, reload],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}

export function useAuth(): AuthApi {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth needs an AuthProvider')
  return value
}

/** The signed-in user; only call under `RequireAuth`. */
export function useMe(): Me {
  const { state } = useAuth()
  if (state.status !== 'authenticated') throw new Error('useMe needs a signed-in user')
  return state.me
}
