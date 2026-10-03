import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { useAuth, useMe } from '../../auth/AuthProvider'
import { FormError, SubmitButton, TextField } from '../public/fields'
import { Modal, useToast } from '../ui'

function download(codes: string[], who: string) {
  const text = [
    'Tarn AI Evaluation recovery codes',
    `Account: ${who}`,
    'Each code works once. Keep them somewhere safe and private.',
    '',
    ...codes,
    '',
  ].join('\n')
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'tarn-recovery-codes.txt'
  link.click()
  URL.revokeObjectURL(url)
}

/**
 * Issues ten one-time recovery codes (needs the current password) and shows them once. The codes
 * live in component state only and are dropped when the dialog closes.
 */
export function RecoveryCodesModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useMe()
  const { reload } = useAuth()
  const toast = useToast()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [codes, setCodes] = useState<string[] | null>(null)
  const [saved, setSaved] = useState(false)

  function close() {
    setPassword('')
    setError(null)
    setCodes(null)
    setSaved(false)
    onClose()
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const {
        data,
        error: err,
        response,
      } = await api.POST('/api/v1/auth/recovery-codes', {
        body: { current_password: password },
      })
      if (data) {
        setCodes(data.codes)
        setPassword('')
        void reload()
      } else if (response.status === 403) setError('The current password is not correct.')
      else setError(problemOf(err, 'Could not create recovery codes. Try again.').message)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  const left = me.recovery_codes_left
  return (
    <Modal open={open} onClose={close} eyebrow="Account security" title="Recovery codes">
      {codes ? (
        <div className="space-y-4">
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/30 p-3 text-xs text-amber-200">
            <i
              className="fa-solid fa-triangle-exclamation mr-2 text-amber-400"
              aria-hidden="true"
            />
            These codes are shown <strong>once</strong>. Save them now: each works one time to reset
            your password if you lose access. The previous set no longer works.
          </div>
          <ul
            aria-label="Recovery codes"
            className="grid grid-cols-1 gap-2 rounded-xl border border-gray-800 bg-gray-950 p-4 font-mono text-sm text-cyan-200 sm:grid-cols-2"
          >
            {codes.map((code) => (
              <li key={code}>{code}</li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard
                  ?.writeText(codes.join('\n'))
                  .then(() => toast.show('Codes copied.'))
                  .catch(() => toast.show('Could not copy. Select the codes by hand.', 'amber'))
              }}
              className="rounded-xl border border-gray-700 bg-gray-800 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-gray-700"
            >
              <i className="fa-solid fa-copy mr-2" aria-hidden="true" /> Copy
            </button>
            <button
              type="button"
              onClick={() => download(codes, me.user.email)}
              className="rounded-xl border border-gray-700 bg-gray-800 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-gray-700"
            >
              <i className="fa-solid fa-download mr-2" aria-hidden="true" /> Download
            </button>
          </div>
          <label className="flex cursor-pointer items-center text-xs text-gray-300">
            <input
              type="checkbox"
              checked={saved}
              onChange={(e) => setSaved(e.target.checked)}
              className="mr-2 rounded border-gray-700 bg-gray-900 text-purple-600 focus:ring-0"
            />
            I have saved these codes
          </label>
          <button
            type="button"
            disabled={!saved}
            onClick={close}
            className="w-full rounded-xl bg-purple-600 py-2.5 text-xs font-bold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Done
          </button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <p className="text-xs text-gray-300">
            Recovery codes let you set a new password if you forget it and cannot use the emailed
            link. You have{' '}
            <strong className="text-white">
              {left} unused code{left === 1 ? '' : 's'}
            </strong>
            . Creating a new set of ten replaces them.
          </p>
          <TextField
            label="Current Password"
            icon="fa-solid fa-key"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
          <FormError message={error} />
          <SubmitButton busy={busy}>Create new recovery codes</SubmitButton>
        </form>
      )}
    </Modal>
  )
}
