import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { CancelButton, RowButton, SubmitInDialog } from '../../pages/admin/controls'
import {
  REQUEST_STATE,
  unitLabel,
  useDelegationRequests,
  type WorkUnit,
} from '../../pages/admin/data'
import { REASON_MAX } from '../../pages/admin/TransferDialog'
import { FormError } from '../public/fields'
import { SelectInput, TextArea } from '../schema/controls'
import { Badge, Modal, useToast } from '../ui'
import { useDelegates } from './queries'

async function refresh(queryClient: ReturnType<typeof useQueryClient>) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['units'] }),
    queryClient.invalidateQueries({ queryKey: ['unit'] }),
    queryClient.invalidateQueries({ queryKey: ['delegation-requests'] }),
  ])
}

/**
 * Delegate (mock modal 4, D218): ask the admin to move the unit to someone else. The SME may
 * suggest a replacement among the active SMEs who have the unit's subject (names only); the
 * reason is required. The admin decides, and sees the suggestion pre-filled.
 */
export function DelegateDialog({ unit, onClose }: { unit: WorkUnit; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const delegates = useDelegates(unit.id)
  const [suggested, setSuggested] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const none = delegates.data !== undefined && delegates.data.length === 0

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await api.POST('/api/v1/units/{unit_id}/delegation-requests', {
        params: { path: { unit_id: unit.id } },
        body: { reason: reason.trim(), ...(suggested ? { suggested_to: suggested } : {}) },
      })
      if (data) {
        toast.show(`Request sent: your admin decides who takes ${unit.code}.`)
        await refresh(queryClient)
        onClose()
      } else setError(problemOf(err, 'Could not send the request.').message)
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
      eyebrow="Delegate Assignment"
      title={`Delegate ${unit.code}`}
      description="The request goes to your admin, who chooses the SME. Until then the unit stays yours; what you save stays with the unit."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="delegate-form" busy={busy} disabled={!reason.trim()}>
            Submit request to admin
          </SubmitInDialog>
        </div>
      }
    >
      <form id="delegate-form" onSubmit={submit} className="space-y-4">
        <div>
          <p className="text-[11px] font-semibold tracking-wider text-gray-400 uppercase">
            Target assignment
          </p>
          <p className="text-sm text-white">{unitLabel(unit)}</p>
          <p className="text-xs text-gray-400">
            {unit.paper.title} · {unit.subject.name} · due {unit.due_date}
          </p>
        </div>
        <SelectInput
          label="Suggested replacement (optional)"
          value={suggested}
          disabled={none || delegates.isError}
          hint={
            delegates.isError
              ? 'The list could not be loaded; you can still send the request.'
              : none
                ? `No other active SME has ${unit.subject.name}: your admin decides.`
                : `SMEs who have ${unit.subject.name}. Your admin may choose someone else.`
          }
          onChange={(e) => setSuggested(e.target.value)}
        >
          <option value="">No suggestion</option>
          {(delegates.data ?? []).map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </SelectInput>
        <TextArea
          label="Reason for transfer"
          value={reason}
          required
          rows={3}
          maxLength={REASON_MAX}
          placeholder="e.g. workload conflict, topic outside my area, leave from next week"
          onChange={(e) => setReason(e.target.value)}
        />
        <FormError message={error} />
      </form>
    </Modal>
  )
}

/** While a request waits: the badge and a Withdraw button. */
export function PendingRequest({ unit }: { unit: WorkUnit }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const pending = unit.pending_request
  if (!pending) return null

  async function withdraw() {
    if (!pending) return
    setBusy(true)
    try {
      const { error, response } = await api.POST(
        '/api/v1/delegation-requests/{request_id}/withdraw',
        { params: { path: { request_id: pending.id } } },
      )
      if (response.ok) toast.show(`Request for ${unit.code} withdrawn.`)
      else toast.show(problemOf(error, 'Could not withdraw the request.').message, 'red')
      await refresh(queryClient)
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge tone="amber">Reassignment requested</Badge>
      <RowButton
        aria-label={`Withdraw the request for ${unit.code}`}
        disabled={busy}
        onClick={() => void withdraw()}
      >
        Withdraw
      </RowButton>
    </span>
  )
}

/** The SME's own requests and what became of them (moved from the old step 1). */
export function MyRequests() {
  const requests = useDelegationRequests()
  if (!requests.data || requests.data.length === 0) return null
  return (
    <section aria-label="My reassignment requests" className="space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold tracking-wider text-gray-400 uppercase">
        <i className="fa-solid fa-inbox text-cyan-400" aria-hidden="true" />
        My reassignment requests
      </h2>
      <ul className="space-y-2">
        {requests.data.map((r) => {
          const state = REQUEST_STATE[r.state]
          return (
            <li
              key={r.id}
              className="glass-panel space-y-1 rounded-xl border border-gray-800 p-3 text-xs"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold text-white">
                  {r.unit.code} · {r.unit.paper_title}
                </span>
                <Badge tone={state.tone}>{state.label}</Badge>
              </div>
              <p className="text-gray-300">{r.reason}</p>
              {r.suggested && <p className="text-gray-400">You suggested {r.suggested.name}.</p>}
              {r.note && <p className="text-red-300">Admin's note: {r.note}</p>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
