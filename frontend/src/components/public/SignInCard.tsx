import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../../auth/AuthProvider'
import { useHealth } from '../../hooks/useHealth'
import { Badge } from '../ui'
import { FormError, SubmitButton, TextField } from './fields'

function PortalBadge() {
  const health = useHealth()
  if (health.phase === 'up') return <Badge tone="emerald">Active Portal</Badge>
  if (health.phase === 'down') return <Badge tone="red">Portal unavailable</Badge>
  return <Badge tone="gray">Checking…</Badge>
}

/** The "Customer Sign In" card. */
export function SignInCard() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [institutionId, setInstitutionId] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const result = await signIn({ institutionId, email, password, remember })
    setBusy(false)
    if (result.kind === 'error') {
      setError(result.message)
      setPassword('')
    } else if (result.kind === 'reset_required') {
      navigate('/reset-password', { state: { token: result.resetToken, forced: true } })
    } else {
      const from = (location.state as { from?: string } | null)?.from
      navigate(from ?? '/qna', { replace: true })
    }
  }

  return (
    <div id="loginSection" className="lg:col-span-5">
      <div className="glass-box relative overflow-hidden rounded-3xl border border-purple-500/30 p-8 shadow-2xl">
        <div
          aria-hidden="true"
          className="absolute -top-12 -right-12 h-32 w-32 rounded-full bg-purple-500/20 blur-2xl"
        />
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-xl font-bold text-white">
              <i className="fa-solid fa-right-to-bracket text-purple-400" aria-hidden="true" />{' '}
              Customer Sign In
            </h2>
            <p className="mt-1 text-xs text-gray-400">
              Access your institutional evaluation portal
            </p>
          </div>
          <PortalBadge />
        </div>

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
          <TextField
            label="Security Access Password"
            icon="fa-solid fa-key"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            placeholder="••••••••••••"
            autoComplete="current-password"
          />

          <div className="flex items-center justify-between pt-1 text-xs">
            <label className="flex cursor-pointer items-center text-gray-400">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="mr-2 rounded border-gray-700 bg-gray-900 text-purple-600 focus:ring-0"
              />{' '}
              Remember session
            </label>
            <Link
              to="/forgot-password"
              className="font-semibold text-purple-400 hover:text-purple-300"
            >
              Forgot Password?
            </Link>
          </div>

          <FormError message={error} />
          <SubmitButton busy={busy}>
            <span>Authenticate Cloud Access</span>
            <i className="fa-solid fa-arrow-right" aria-hidden="true" />
          </SubmitButton>
        </form>
      </div>
    </div>
  )
}
