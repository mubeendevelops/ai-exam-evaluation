import { useState, type FormEvent, type ReactNode } from 'react'
import { FormError, SubmitButton, TextField } from './fields'
import { PasswordHint } from './PasswordHint'

interface Props {
  /** Fields above the password (e.g. the recovery code form's Institution ID). */
  before?: ReactNode
  submitLabel: string
  busy: boolean
  error: { message: string; reasons: string[] } | null
  onSubmit: (password: string) => void
  passwordLabel?: string
}

/** New password + confirmation + strength hint, shared by reset, recovery and invitation screens. */
export function NewPasswordForm({
  before,
  submitLabel,
  busy,
  error,
  onSubmit,
  passwordLabel = 'New Password',
}: Props) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const mismatch = confirm !== '' && confirm !== password

  function submit(event: FormEvent) {
    event.preventDefault()
    if (password !== confirm) return
    onSubmit(password)
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      {before}
      <div>
        <TextField
          label={passwordLabel}
          icon="fa-solid fa-key"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          placeholder="••••••••••••"
          autoComplete="new-password"
        />
        <PasswordHint password={password} />
      </div>
      <TextField
        label="Confirm Password"
        icon="fa-solid fa-key"
        type="password"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
        required
        placeholder="••••••••••••"
        autoComplete="new-password"
        error={mismatch ? 'The two passwords do not match.' : null}
      />
      <FormError message={error?.message ?? null} reasons={error?.reasons} />
      <SubmitButton busy={busy} disabled={mismatch}>
        {submitLabel}
      </SubmitButton>
    </form>
  )
}
