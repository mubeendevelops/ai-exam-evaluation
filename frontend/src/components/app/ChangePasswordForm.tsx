import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { FormError, SubmitButton, TextField } from '../public/fields'
import { PasswordHint } from '../public/PasswordHint'
import { useToast } from '../ui'

/**
 * The signed-in user changes their password (needs the current one). Shared by the profile
 * dialog and the SME's My Profile page; the fields are cleared once it works.
 */
export function ChangePasswordForm({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } = await api.POST('/api/v1/auth/password/change', {
        body: { current_password: current, new_password: next },
      })
      if (response.ok) {
        toast.show('Password changed.')
        setCurrent('')
        setNext('')
        onChanged?.()
      } else if (response.status === 403) {
        setError({ message: 'The current password is not correct.', reasons: [] })
      } else setError(problemOf(err, 'The password was not changed. Try again.'))
    } catch {
      setError({ message: NETWORK_PROBLEM, reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <h3 className="text-sm font-bold tracking-wider text-purple-400 uppercase">
        Change password
      </h3>
      <TextField
        label="Current Password"
        type="password"
        value={current}
        onChange={(e) => setCurrent(e.target.value)}
        required
        autoComplete="current-password"
      />
      <div>
        <TextField
          label="New Password"
          type="password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
          autoComplete="new-password"
        />
        <PasswordHint password={next} />
      </div>
      <FormError message={error?.message ?? null} reasons={error?.reasons} />
      <SubmitButton busy={busy}>Change password</SubmitButton>
    </form>
  )
}
