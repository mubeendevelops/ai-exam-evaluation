import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { FormError } from '../../components/public/fields'
import { SelectInput, TextArea } from '../../components/schema/controls'
import { Modal, useToast } from '../../components/ui'
import { CancelButton, SubmitInDialog } from './controls'
import { useEntitledSmes, type WorkUnit } from './data'

export const REASON_MAX = 500

/**
 * Move an open unit to another SME (P27, D163): only active SMEs entitled to its subject are
 * offered (D160); the reason is required and audited, never emailed. A pending delegation
 * request for the unit closes as done. From a request, the SME's suggestion (D218) is picked
 * first while that SME can still take the unit; the admin may choose someone else.
 */
export function TransferDialog({
  unit,
  initialReason = '',
  suggested,
  onClose,
}: {
  unit: WorkUnit
  initialReason?: string
  /** The SME the requester suggested. */
  suggested?: { id: string; name: string } | null
  onClose: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const smes = useEntitledSmes(unit.subject.id)
  const [picked, setPicked] = useState<string | null>(null)
  const [reason, setReason] = useState(initialReason)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const targets = (smes.data ?? []).filter((s) => s.id !== unit.owner.id)
  const suggestion = suggested && targets.some((t) => t.id === suggested.id) ? suggested : null
  const ownerId = picked ?? suggestion?.id ?? ''

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await api.POST('/api/v1/units/{unit_id}/transfer', {
        params: { path: { unit_id: unit.id } },
        body: { owner_id: ownerId, reason: reason.trim(), expected_version: unit.version },
      })
      if (data) {
        toast.show(`Unit ${data.code} moved to ${data.owner.name}; both SMEs were emailed.`)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['units'] }),
          queryClient.invalidateQueries({ queryKey: ['delegation-requests'] }),
        ])
        onClose()
      } else setError(problemOf(err, 'Could not move the unit.').message)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  const none = smes.data !== undefined && targets.length === 0
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Redistribute Workload"
      title={`Transfer ${unit.code}`}
      description={`${unit.paper.title} · ${unit.subject.name} · ${unit.year}. Now with ${unit.owner.name}. Every booklet moves as it stands; ${unit.owner.name} loses access at once.`}
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="transfer-form" busy={busy} disabled={!ownerId || !reason.trim()}>
            Transfer unit
          </SubmitInDialog>
        </div>
      }
    >
      <form id="transfer-form" onSubmit={submit} className="space-y-4">
        <SelectInput
          label="New SME"
          value={ownerId}
          required
          disabled={none}
          hint={
            none
              ? 'No other active SME has this subject: grant it in the SME Directory first.'
              : suggestion
                ? `${suggestion.name} was suggested by ${unit.owner.name}; you may choose someone else. Both SMEs are emailed.`
                : 'Only active SMEs who have this subject are offered. Both SMEs are emailed.'
          }
          onChange={(e) => setPicked(e.target.value)}
        >
          <option value="">Choose an SME…</option>
          {targets.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.email})
            </option>
          ))}
        </SelectInput>
        <TextArea
          label="Reason for the transfer"
          value={reason}
          required
          rows={3}
          maxLength={REASON_MAX}
          placeholder="e.g. SME on leave, capacity, overdue risk"
          hint="Kept in the audit log; not sent in the emails."
          onChange={(e) => setReason(e.target.value)}
        />
        <FormError message={error} />
      </form>
    </Modal>
  )
}
