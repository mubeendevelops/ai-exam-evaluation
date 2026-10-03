import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { useMe } from '../../auth/AuthProvider'
import { Badge, Modal, useToast } from '../ui'
import { FormError, SubmitButton, TextField } from '../public/fields'
import { PasswordHint } from '../public/PasswordHint'

/** Who am I, which college, and change password. */
export function ProfileModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useMe()
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)

  function close() {
    setCurrent('')
    setNext('')
    setError(null)
    onClose()
  }

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
        close()
      } else if (response.status === 403) {
        setError({ message: 'The current password is not correct.', reasons: [] })
      } else setError(problemOf(err, 'The password was not changed. Try again.'))
    } catch {
      setError({ message: NETWORK_PROBLEM, reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  const { user } = me
  const rows: [string, string][] = [
    ['Name', user.display_name],
    ['Email', user.email],
    ['College', me.college_name],
    ['Institution ID', me.institution_id],
  ]
  return (
    <Modal open={open} onClose={close} eyebrow="Account" title="Profile">
      <dl className="mb-6 space-y-2 text-xs">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 border-b border-gray-800 pb-2">
            <dt className="text-gray-400">{label}</dt>
            <dd className="truncate font-semibold text-white">{value}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-4 border-b border-gray-800 pb-2">
          <dt className="text-gray-400">Role</dt>
          <dd>
            <Badge tone={user.role === 'admin' ? 'amber' : 'purple'}>{user.role}</Badge>
          </dd>
        </div>
      </dl>

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
    </Modal>
  )
}
