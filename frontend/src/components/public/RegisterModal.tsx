import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { INSTITUTION_ID_MAX, institutionIdProblem } from '../../lib/institutionId'
import { Modal } from '../ui'
import { FormError, SubmitButton, TextField } from './fields'
import { PasswordHint } from './PasswordHint'

type Availability = { tone: 'ok' | 'taken' | 'bad'; text: string } | null

interface Registered {
  institutionId: string
  email: string
  approvalRequired: boolean
}

function RegistrationDone({ done, onClose }: { done: Registered; onClose: () => void }) {
  return (
    <div className="space-y-4 text-sm text-gray-300">
      <div className="flex items-start gap-3 rounded-xl border border-emerald-500/30 bg-emerald-950/30 p-4">
        <i
          className="fa-solid fa-envelope-circle-check mt-0.5 text-xl text-emerald-400"
          aria-hidden="true"
        />
        <div className="space-y-2 text-xs">
          <p className="text-sm font-bold text-white">Check your inbox to verify your email</p>
          <p>
            We sent a verification link to <strong className="text-white">{done.email}</strong> for
            workspace <strong className="font-mono text-white">{done.institutionId}</strong>. The
            link works once.
          </p>
          {done.approvalRequired && (
            <p>
              After you verify, a Tarn operator reviews the registration. You can sign in once the
              workspace is active.
            </p>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="w-full rounded-xl bg-gray-800 py-2.5 text-xs font-semibold text-gray-200 hover:bg-gray-700"
      >
        Close
      </button>
    </div>
  )
}

/** "Tenant Registration" modal: live Institution ID validation, availability check, strength hint. */
export function RegisterModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [institutionId, setInstitutionId] = useState('')
  const [institutionName, setInstitutionName] = useState('')
  const [adminName, setAdminName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [availability, setAvailability] = useState<Availability>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)
  const [done, setDone] = useState<Registered | null>(null)

  const formatProblem = institutionIdProblem(institutionId)

  function close() {
    onClose()
    if (done) {
      setDone(null)
      setInstitutionId('')
      setInstitutionName('')
      setAdminName('')
      setEmail('')
      setAvailability(null)
    }
    setPassword('')
    setError(null)
  }

  function changeInstitutionId(value: string) {
    setInstitutionId(value)
    setAvailability(null)
  }

  async function checkAvailability() {
    if (institutionId === '' || formatProblem) {
      setAvailability({
        tone: 'bad',
        text: formatProblem ?? 'Enter an Institution ID first.',
      })
      return
    }
    try {
      const { data, error: err } = await api.GET('/api/v1/registrations/availability', {
        params: { query: { institution_id: institutionId.trim() } },
      })
      if (!data) {
        setAvailability({
          tone: 'bad',
          text: problemOf(err, 'Could not check availability.').message,
        })
      } else if (!data.valid) {
        setAvailability({ tone: 'bad', text: data.problem ?? 'Invalid Institution ID.' })
      } else if (!data.available) {
        setAvailability({
          tone: 'taken',
          text: `Org ID "${data.institution_id}" is ALREADY TAKEN. Please choose another.`,
        })
      } else {
        setAvailability({ tone: 'ok', text: `Org ID "${data.institution_id}" is AVAILABLE!` })
      }
    } catch {
      setAvailability({ tone: 'bad', text: NETWORK_PROBLEM })
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (formatProblem) {
      setError({
        message: 'Please correct the Institution ID errors before proceeding.',
        reasons: [],
      })
      return
    }
    setBusy(true)
    setError(null)
    try {
      const {
        data,
        error: err,
        response,
      } = await api.POST('/api/v1/registrations', {
        body: {
          institution_id: institutionId.trim(),
          institution_name: institutionName.trim() || null,
          admin_name: adminName.trim(),
          email: email.trim(),
          password,
        },
      })
      if (data) {
        setDone({
          institutionId: data.institution_id,
          email: email.trim(),
          approvalRequired: data.approval_required,
        })
        setPassword('')
      } else if (response.status === 409) {
        setAvailability({
          tone: 'taken',
          text: `Org ID "${institutionId.trim()}" is ALREADY TAKEN. Please choose another.`,
        })
        setError({ message: problemOf(err, 'This Institution ID is taken.').message, reasons: [] })
      } else {
        setError(problemOf(err, 'Registration failed. Check the details and try again.'))
      }
    } catch {
      setError({ message: NETWORK_PROBLEM, reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  const feedback = formatProblem
    ? { tone: 'bad' as const, text: formatProblem }
    : institutionId !== '' && !availability
      ? { tone: 'format' as const, text: 'Format matches specification.' }
      : availability
  const feedbackColor = {
    bad: 'text-red-400',
    taken: 'text-red-400',
    format: 'text-cyan-400',
    ok: 'text-emerald-400',
  }

  return (
    <Modal
      open={open}
      onClose={close}
      variant="public"
      eyebrow="Tenant Registration"
      title={done ? 'Registration received' : 'Create Cloud Workspace'}
      description={
        done
          ? undefined
          : 'Register your organization domain and setup evaluator admin credentials.'
      }
    >
      {done ? (
        <RegistrationDone done={done} onClose={close} />
      ) : (
        <>
          <form onSubmit={submit} className="space-y-4">
            <div>
              <TextField
                label="Institution ID / Org Domain ID"
                value={institutionId}
                onChange={(e) => changeInstitutionId(e.target.value)}
                maxLength={INSTITUTION_ID_MAX}
                required
                requiredMark
                placeholder="e.g. TARN_INST_01"
                spellCheck={false}
                autoComplete="off"
                hint="Max 20 chars, no spaces. Allowed: A-Z, 0-9, _, -, *, &"
                trailing={
                  <button
                    type="button"
                    onClick={checkAvailability}
                    className="rounded-lg border border-purple-500/40 bg-purple-900/60 px-2.5 py-1.5 text-[10px] text-purple-200 transition hover:bg-purple-800"
                  >
                    Check
                  </button>
                }
              />
              <div
                role="status"
                className={`mt-1 text-[11px] font-semibold ${feedback ? feedbackColor[feedback.tone] : ''}`}
              >
                {feedback?.text}
              </div>
            </div>

            <TextField
              label="College / Institution Name"
              value={institutionName}
              onChange={(e) => setInstitutionName(e.target.value)}
              maxLength={200}
              placeholder="Optional: shown in the app next to your name"
              autoComplete="organization"
            />

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <TextField
                label="Admin Full Name"
                value={adminName}
                onChange={(e) => setAdminName(e.target.value)}
                required
                placeholder="Dr. Sarah Connor"
                autoComplete="name"
              />
              <TextField
                label="Official Email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="admin@domain.edu"
                autoComplete="email"
              />
            </div>

            <div>
              <TextField
                label="Security Password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="••••••••••••"
                autoComplete="new-password"
              />
              <PasswordHint password={password} />
            </div>

            <FormError message={error?.message ?? null} reasons={error?.reasons} />
            <div className="pt-2">
              <SubmitButton busy={busy}>
                Complete Registration &amp; Provision Workspace
              </SubmitButton>
            </div>
          </form>
        </>
      )}
    </Modal>
  )
}
