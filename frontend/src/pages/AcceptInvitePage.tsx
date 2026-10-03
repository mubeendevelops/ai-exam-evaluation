import { useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client'
import { NETWORK_PROBLEM, problemOf } from '../api/errors'
import { AuthScreen, BackToSignIn, Notice } from '../components/public/AuthScreen'
import { NewPasswordForm } from '../components/public/NewPasswordForm'
import { useLinkToken } from '../hooks/useLinkToken'

/** `/accept-invite`: a teacher invited by an administrator sets the first password. */
export default function AcceptInvitePage() {
  const { token } = useLinkToken()
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)

  async function submit(password: string) {
    if (!token) return
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } = await api.POST('/api/v1/auth/invitations/accept', {
        body: { token, password },
      })
      if (response.ok) setDone(true)
      else if (response.status === 400) {
        setError({
          message:
            'This invitation is invalid, has expired, or was already used. Ask your administrator to send a new one.',
          reasons: [],
        })
      } else setError(problemOf(err, 'The password was not saved. Try again.'))
    } catch {
      setError({ message: NETWORK_PROBLEM, reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <AuthScreen icon="fa-solid fa-circle-check" title="Welcome aboard">
        <Notice icon="fa-solid fa-user-check">
          <p>Your account is ready. Sign in with your Institution ID, email and new password.</p>
        </Notice>
        <BackToSignIn label="Go to sign in" />
      </AuthScreen>
    )
  }

  if (!token) {
    return (
      <AuthScreen icon="fa-solid fa-link-slash" title="Invitation link incomplete">
        <Notice tone="amber" icon="fa-solid fa-triangle-exclamation">
          <p>This page needs the link from the invitation email.</p>
        </Notice>
        <p className="mt-5 text-xs text-gray-400">
          <Link to="/" className="font-semibold text-purple-400 hover:text-purple-300">
            Back to sign in
          </Link>
        </p>
      </AuthScreen>
    )
  }

  return (
    <AuthScreen
      icon="fa-solid fa-user-plus"
      title="Accept your invitation"
      subtitle="Choose a password to finish setting up your account."
    >
      <NewPasswordForm
        busy={busy}
        error={error}
        onSubmit={(p) => void submit(p)}
        submitLabel="Create my account"
        passwordLabel="Password"
      />
    </AuthScreen>
  )
}
