import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { CancelButton, SubmitInDialog } from '../../pages/admin/controls'
import { unitLabel, type WorkUnit } from '../../pages/admin/data'
import { REASON_MAX } from '../../pages/admin/TransferDialog'
import { FormError } from '../public/fields'
import { SelectInput, TextArea } from '../schema/controls'
import { Modal, useToast } from '../ui'
import { STATUS, type Booklet } from '../evaluate/model'
import { useUnitDetail } from './queries'

type Reason = 'absent' | 'unreadable' | 'other'
const REASONS: { value: Reason; label: string }[] = [
  { value: 'absent', label: 'Absent' },
  { value: 'unreadable', label: 'Unreadable' },
  { value: 'other', label: 'Other (say why)' },
]
const MARKED = new Set<Booklet['status']>(['approved', 'approved_amended'])

/**
 * Submit the whole unit to the admin (P28, D164). Every booklet is approved or given a reason
 * it was not marked. After this the SME cannot change anything in the unit unless the admin
 * returns it, so the dialog says so before sending.
 */
export function SubmitDialog({
  unit,
  booklets,
  onClose,
}: {
  unit: WorkUnit
  booklets: Booklet[]
  onClose: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [reasons, setReasons] = useState<Record<string, { reason: Reason | ''; note: string }>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const working = booklets.filter((b) => STATUS[b.status].working)
  const drafts = booklets.filter((b) => b.status === 'amendment_in_progress')
  const unmarked = booklets.filter(
    (b) =>
      !MARKED.has(b.status) && !STATUS[b.status].working && b.status !== 'amendment_in_progress',
  )
  const blocked = working.length > 0 || drafts.length > 0
  const complete = unmarked.every((b) => {
    const r = reasons[b.id]
    return r?.reason && (r.reason !== 'other' || r.note.trim())
  })

  function set(id: string, change: Partial<{ reason: Reason | ''; note: string }>) {
    setReasons((all) => ({ ...all, [id]: { reason: '', note: '', ...all[id], ...change } }))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await api.POST('/api/v1/units/{unit_id}/submit', {
        params: { path: { unit_id: unit.id } },
        body: {
          expected_version: unit.version,
          unmarked: unmarked.map((b) => ({
            booklet_id: b.id,
            reason: reasons[b.id]!.reason as Reason,
            note: reasons[b.id]!.note.trim(),
          })),
        },
      })
      if (data) {
        toast.show(`${unit.code} submitted to your admin.`)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['units'] }),
          queryClient.invalidateQueries({ queryKey: ['me'] }),
          queryClient.invalidateQueries({ queryKey: ['unit', unit.id] }),
        ])
        onClose()
      } else setError(problemOf(err, 'Could not submit the unit.').message)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Submit unit"
      title={`Submit ${unitLabel(unit)}`}
      description="The whole unit goes to your admin. After this you cannot change, reopen or amend anything in it unless the admin returns it."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="submit-unit-form" busy={busy} disabled={blocked || !complete}>
            Submit unit
          </SubmitInDialog>
        </div>
      }
    >
      <form id="submit-unit-form" onSubmit={submit} className="space-y-4">
        {working.length > 0 && (
          <p role="alert" className="text-xs text-amber-200">
            {working.length} booklet(s) are still being processed: wait until they are ready.
          </p>
        )}
        {drafts.length > 0 && (
          <p role="alert" className="text-xs text-amber-200">
            {drafts.length} booklet(s) have an amendment in progress: approve or withdraw the draft
            first.
          </p>
        )}
        <p className="text-xs text-gray-300">
          {booklets.length - unmarked.length - working.length - drafts.length} of {booklets.length}{' '}
          booklets are marked.
          {unmarked.length > 0 && ' Give a reason for each booklet that is not:'}
        </p>
        {unmarked.length > 0 && (
          <ul aria-label="Booklets not marked" className="space-y-3">
            {unmarked.map((b) => {
              const r = reasons[b.id] ?? { reason: '', note: '' }
              return (
                <li key={b.id} className="space-y-2 rounded-xl border border-gray-800 p-3">
                  <p className="text-xs text-gray-200">
                    <span className="font-mono">{b.student.usn}</span> · {b.student.name} ·{' '}
                    {STATUS[b.status].label}
                  </p>
                  <SelectInput
                    label={`Reason for ${b.student.usn}`}
                    value={r.reason}
                    required
                    onChange={(e) => set(b.id, { reason: e.target.value as Reason })}
                  >
                    <option value="">Choose a reason…</option>
                    {REASONS.map((x) => (
                      <option key={x.value} value={x.value}>
                        {x.label}
                      </option>
                    ))}
                  </SelectInput>
                  {r.reason === 'other' && (
                    <TextArea
                      label={`Note for ${b.student.usn}`}
                      value={r.note}
                      required
                      rows={2}
                      maxLength={REASON_MAX}
                      onChange={(e) => set(b.id, { note: e.target.value })}
                    />
                  )}
                </li>
              )
            })}
          </ul>
        )}
        <FormError message={error} />
      </form>
    </Modal>
  )
}

/** Submit needs every booklet of the unit (which to give a reason for): load them, then the
 * dialog. Opened from My Assignments and from the student list. */
export function SubmitUnit({ unit, onClose }: { unit: WorkUnit; onClose: () => void }) {
  const detail = useUnitDetail(unit.id, { all: true })
  if (detail.isError) {
    return (
      <p role="alert" className="mt-2 text-xs text-red-300">
        The booklets of {unit.code} could not be loaded.
      </p>
    )
  }
  if (!detail.data) return null
  return <SubmitDialog unit={unit} booklets={detail.data.booklets} onClose={onClose} />
}
