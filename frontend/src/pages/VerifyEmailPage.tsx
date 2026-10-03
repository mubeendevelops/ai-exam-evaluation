import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client'
import { NETWORK_PROBLEM } from '../api/errors'
import { AuthScreen, BackToSignIn, Notice } from '../components/public/AuthScreen'
import { useLinkToken } from '../hooks/useLinkToken'

type Outcome =
  | { phase: 'verifying' }
  | { phase: 'done'; institutionId: string; status: string }
  | { phase: 'failed'; message: string }

// The link works once, and React StrictMode runs effects twice in development: one request per token.
const attempts = new Map<string, Promise<Outcome>>()

function verify(token: string): Promise<Outcome> {
  let attempt = attempts.get(token)
  if (!attempt) {
    attempt = api
      .POST('/api/v1/registrations/verify-email', { body: { token } })
      .then(({ data, response }): Outcome => {
        if (data) return { phase: 'done', institutionId: data.institution_id, status: data.status }
        return {
          phase: 'failed',
          message:
            response.status === 400
              ? 'This verification link is invalid, has expired, or was already used.'
              : 'The email could not be verified. Try the link again later.',
        }
      })
      .catch((): Outcome => ({ phase: 'failed', message: NETWORK_PROBLEM }))
    attempts.set(token, attempt)
  }
  return attempt
}

/** `/verify-email`: the link in the registration email confirms the admin's address. */
export default function VerifyEmailPage() {
  const { token } = useLinkToken()
  const [outcome, setOutcome] = useState<Outcome>({ phase: 'verifying' })

  useEffect(() => {
    if (!token) return
    let cancelled = false
    void verify(token).then((result) => {
      if (!cancelled) setOutcome(result)
    })
    return () => {
      cancelled = true
    }
  }, [token])

  if (!token) {
    return (
      <AuthScreen icon="fa-solid fa-link-slash" title="Verification link incomplete">
        <Notice tone="amber" icon="fa-solid fa-triangle-exclamation">
          <p>This page needs the link from the verification email.</p>
        </Notice>
        <BackToSignIn />
      </AuthScreen>
    )
  }

  if (outcome.phase === 'verifying') {
    return (
      <AuthScreen icon="fa-solid fa-envelope-circle-check" title="Verifying your email">
        <p role="status" className="text-xs text-gray-300">
          <i className="fa-solid fa-circle-notch fa-spin mr-2 text-purple-400" aria-hidden="true" />
          One moment…
        </p>
      </AuthScreen>
    )
  }

  if (outcome.phase === 'failed') {
    return (
      <AuthScreen icon="fa-solid fa-circle-xmark" title="Email not verified">
        <Notice tone="red" icon="fa-solid fa-triangle-exclamation">
          <p>{outcome.message}</p>
        </Notice>
        <BackToSignIn />
      </AuthScreen>
    )
  }

  const active = outcome.status === 'ACTIVE'
  return (
    <AuthScreen icon="fa-solid fa-circle-check" title="Email verified">
      <Notice icon="fa-solid fa-envelope-circle-check">
        <p>
          Workspace <strong className="font-mono text-white">{outcome.institutionId}</strong> has a
          verified administrator email.
        </p>
        {active ? (
          <p>The workspace is active. You can sign in now.</p>
        ) : (
          <p>
            A Tarn operator now reviews the registration. You can sign in once the workspace is
            active.
          </p>
        )}
      </Notice>
      {active ? (
        <Link
          to="/"
          className="mt-5 inline-flex items-center gap-2 text-xs font-semibold text-purple-400 hover:text-purple-300"
        >
          Go to sign in <i className="fa-solid fa-arrow-right" aria-hidden="true" />
        </Link>
      ) : (
        <BackToSignIn label="Back to the landing page" />
      )}
    </AuthScreen>
  )
}
