import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client'
import { NETWORK_PROBLEM, problemOf } from '../api/errors'
import { AuthScreen, BackToSignIn, Notice } from '../components/public/AuthScreen'
import { FormError, SubmitButton, TextField } from '../components/public/fields'

/** `/forgot-password`: asks for a reset link by email (the answer never says if the account exists). */
export default function ForgotPasswordPage() {
  const [institutionId, setInstitutionId] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } = await api.POST('/api/v1/auth/password/forgot', {
        body: { institution_id: institutionId.trim(), email: email.trim() },
      })
      if (response.ok) setSent(true)
      else
        setError(
          problemOf(err, 'Could not send the link. Check the details and try again.').message,
        )
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <AuthScreen icon="fa-solid fa-envelope-open-text" title="Check your email">
        <Notice icon="fa-solid fa-paper-plane">
          <p>
            If an account exists for that Institution ID and email, we have sent a link to reset the
            password.
          </p>
          <p>The link works once and expires in 30 minutes. A newer link cancels the older one.</p>
        </Notice>
        <BackToSignIn />
      </AuthScreen>
    )
  }

  return (
    <AuthScreen
      icon="fa-solid fa-key"
      title="Forgot your password?"
      subtitle="We email a link to set a new one."
    >
      <form onSubmit={submit} className="space-y-4">
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
        <FormError message={error} />
        <SubmitButton busy={busy}>
          <span>Email me a reset link</span>
          <i className="fa-solid fa-paper-plane" aria-hidden="true" />
        </SubmitButton>
      </form>
      <p className="mt-5 text-xs text-gray-400">
        Have a recovery code?{' '}
        <Link to="/recover" className="font-semibold text-purple-400 hover:text-purple-300">
          Reset with a recovery code
        </Link>
      </p>
      <BackToSignIn />
    </AuthScreen>
  )
}
