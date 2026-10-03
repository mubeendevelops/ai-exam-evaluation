import { useState } from 'react'
import { api } from '../api/client'
import { NETWORK_PROBLEM, problemOf } from '../api/errors'
import { AuthScreen, BackToSignIn, Notice } from '../components/public/AuthScreen'
import { TextField } from '../components/public/fields'
import { NewPasswordForm } from '../components/public/NewPasswordForm'

/** `/recover`: a one-time recovery code instead of an emailed link. */
export default function RecoverPage() {
  const [institutionId, setInstitutionId] = useState('')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)

  async function submit(newPassword: string) {
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } = await api.POST('/api/v1/auth/password/recover', {
        body: {
          institution_id: institutionId.trim(),
          email: email.trim(),
          recovery_code: code.trim(),
          new_password: newPassword,
        },
      })
      if (response.ok) setDone(true)
      else if (response.status === 401) {
        setError({
          message:
            'Recovery failed. Check the Institution ID, email and code. Wrong codes count towards the account lockout.',
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
          <p>
            Your password was changed and the recovery code is used up. Sign in with the new
            password, and create a fresh set of codes from the user menu.
          </p>
        </Notice>
        <BackToSignIn label="Go to sign in" />
      </AuthScreen>
    )
  }

  return (
    <AuthScreen
      icon="fa-solid fa-life-ring"
      title="Recover with a code"
      subtitle="Use one of the ten one-time recovery codes you saved when you created them."
    >
      <NewPasswordForm
        busy={busy}
        error={error}
        onSubmit={(p) => void submit(p)}
        submitLabel="Reset password"
        before={
          <>
            <TextField
              label="Institution / Org Domain ID"
              icon="fa-solid fa-building"
              value={institutionId}
              onChange={(e) => setInstitutionId(e.target.value)}
              required
              placeholder="e.g. TARN_INST_01"
              autoComplete="organization"
              spellCheck={false}
            />
            <TextField
              label="Username / Evaluator Email"
              icon="fa-solid fa-envelope"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="evaluator@institution.edu"
              autoComplete="username"
            />
            <TextField
              label="Recovery Code"
              icon="fa-solid fa-ticket"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              required
              placeholder="XXXX-XXXX-XXXX-XXXX"
              autoComplete="off"
              spellCheck={false}
              inputClassName="font-mono"
            />
          </>
        }
      />
      <BackToSignIn />
    </AuthScreen>
  )
}
