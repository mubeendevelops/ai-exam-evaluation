import { useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client'
import { NETWORK_PROBLEM, problemOf } from '../api/errors'
import { AuthScreen, BackToSignIn, Notice } from '../components/public/AuthScreen'
import { NewPasswordForm } from '../components/public/NewPasswordForm'
import { useLinkToken } from '../hooks/useLinkToken'

/** `/reset-password`: sets a new password from an emailed link, or after an administrator's forced reset. */
export default function ResetPasswordPage() {
  const { token, forced } = useLinkToken()
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)

  async function submit(newPassword: string) {
    if (!token) return
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } = await api.POST('/api/v1/auth/password/reset', {
        body: { token, new_password: newPassword },
      })
      if (response.ok) setDone(true)
      else if (response.status === 400) {
        setError({
          message: 'This link is invalid, has expired, or was already used. Request a new one.',
          reasons: [],
        })
      } else setError(problemOf(err, 'The password was not changed. Try again.'))
    } catch {
      setError({ message: NETWORK_PROBLEM, reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <AuthScreen icon="fa-solid fa-circle-check" title="Password updated">
        <Notice icon="fa-solid fa-shield-halved">
          <p>Your password was changed. Sign in with the new password.</p>
        </Notice>
        <BackToSignIn label="Go to sign in" />
      </AuthScreen>
    )
  }

  if (!token) {
    return (
      <AuthScreen icon="fa-solid fa-link-slash" title="Reset link incomplete">
        <Notice tone="amber" icon="fa-solid fa-triangle-exclamation">
          <p>This page needs the link from the reset email.</p>
        </Notice>
        <p className="mt-5 text-xs text-gray-400">
          <Link
            to="/forgot-password"
            className="font-semibold text-purple-400 hover:text-purple-300"
          >
            Request a new link
          </Link>
        </p>
        <BackToSignIn />
      </AuthScreen>
    )
  }

  return (
    <AuthScreen
      icon="fa-solid fa-lock"
      title="Set a new password"
      subtitle={
        forced ? 'Your administrator requires a new password before you sign in.' : undefined
      }
    >
      <NewPasswordForm
        busy={busy}
        error={error}
        onSubmit={(p) => void submit(p)}
        submitLabel="Update password"
      />
      <BackToSignIn />
    </AuthScreen>
  )
}
