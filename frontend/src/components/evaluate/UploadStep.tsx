import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { CancelButton, RowButton, SubmitInDialog } from '../../pages/admin/controls'
import {
  REQUEST_STATE,
  unitLabel,
  useDelegationRequests,
  useUnits,
  type WorkUnit,
} from '../../pages/admin/data'
import { REASON_MAX } from '../../pages/admin/TransferDialog'
import { FormError } from '../public/fields'
import { SelectInput, TextArea } from '../schema/controls'
import { Badge, EmptyState, GlassPanel, Modal, useToast } from '../ui'
import { STATUS } from './model'
import { SubmissionCard } from './SubmissionCard'

/**
 * Step 1: the booklets of the SME's open units (P26). The admin uploads them into a unit
 * (D162); the SME picks a unit, sees each student's booklet and its status, and opens one.
 */
export function UploadStep({ onOpen }: { onOpen: (bookletId: string) => void }) {
  const units = useUnits({ state: 'open' })
  const [picked, setPicked] = useState('')
  const unitId = picked || units.data?.[0]?.id || ''
  const unit = units.data?.find((u) => u.id === unitId)
  const [asking, setAsking] = useState(false)

  const booklets = useQuery({
    queryKey: ['booklets', unitId],
    enabled: unitId !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets', {
        params: { query: { unit_id: unitId, limit: 100 } },
      })
      if (!data) throw new Error('booklets unavailable')
      return data
    },
    // The machine works on its own: keep the grid current while any booklet is in its hands.
    refetchInterval: (query) =>
      query.state.data?.items.some((b) => STATUS[b.status].working) ? 4000 : false,
  })
  const list = booklets.data

  return (
    <div className="space-y-6">
      <p className="glass-panel rounded-xl border border-gray-800 p-4 text-xs text-gray-300">
        <i className="fa-solid fa-circle-info mr-2 text-cyan-400" aria-hidden="true" />
        Scanned booklets are uploaded by your admin into the units assigned to you.
      </p>

      {units.isError && (
        <p role="alert" className="text-xs text-red-300">
          Your units could not be loaded.
        </p>
      )}
      {units.data && units.data.length === 0 && (
        <EmptyState icon="fa-solid fa-layer-group" title="No units assigned to you">
          Your admin assigns units of work to you; you are emailed when they do.
        </EmptyState>
      )}

      {units.data && units.data.length > 0 && (
        <GlassPanel
          as="section"
          aria-label="Choose a unit"
          className="grid gap-4 p-4 md:grid-cols-2"
        >
          <SelectInput
            label="Unit"
            value={unitId}
            onChange={(e) => setPicked(e.target.value)}
            hint={
              unit
                ? `${unit.paper.title} · ${unit.subject.name} · due ${unit.due_date} · ${unit.counts.completed} of ${unit.counts.total} completed`
                : undefined
            }
          >
            {units.data.map((u) => (
              <option key={u.id} value={u.id}>
                {unitLabel(u)}
              </option>
            ))}
          </SelectInput>
          {unit && <Reassignment unit={unit} onAsk={() => setAsking(true)} />}
        </GlassPanel>
      )}
      {asking && unit && <RequestDialog unit={unit} onClose={() => setAsking(false)} />}

      {unit && (
        <section aria-label="Student answer submissions">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold tracking-wider text-gray-400 uppercase">
            <i className="fa-solid fa-folder-open text-purple-400" aria-hidden="true" />
            Student Answer Submissions ({list?.total ?? 0})
          </h3>
          {booklets.isError && (
            <p role="alert" className="text-xs text-red-300">
              The submissions could not be loaded.
            </p>
          )}
          {list && list.items.length === 0 && (
            <EmptyState icon="fa-solid fa-file-circle-plus" title="No submissions yet">
              Booklets of this unit appear here once your admin uploads them.
            </EmptyState>
          )}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {list?.items.map((b) => (
              <SubmissionCard key={b.id} booklet={b} onOpen={onOpen} />
            ))}
          </div>
        </section>
      )}
      <MyRequests />
    </div>
  )
}

/**
 * Ask the admin to move the unit to someone else (the delegation inbox, P27). The admin picks
 * the new SME; while the request waits the SME may withdraw it. (The full SME portal's
 * "Delegate" comes in Stage G.)
 */
function Reassignment({ unit, onAsk }: { unit: WorkUnit; onAsk: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const pending = unit.pending_request

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
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['units'] }),
        queryClient.invalidateQueries({ queryKey: ['delegation-requests'] }),
      ])
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-2">
      {pending ? (
        <>
          <Badge tone="amber">Reassignment requested</Badge>
          <RowButton disabled={busy} onClick={() => void withdraw()}>
            Withdraw request
          </RowButton>
        </>
      ) : (
        <RowButton onClick={onAsk}>Request reassignment</RowButton>
      )}
    </div>
  )
}

function RequestDialog({ unit, onClose }: { unit: WorkUnit; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await api.POST('/api/v1/units/{unit_id}/delegation-requests', {
        params: { path: { unit_id: unit.id } },
        body: { reason: reason.trim() },
      })
      if (data) {
        toast.show(`Request sent: your admin decides who takes ${unit.code}.`)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['units'] }),
          queryClient.invalidateQueries({ queryKey: ['delegation-requests'] }),
        ])
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
      eyebrow="Request reassignment"
      title={`Ask to move ${unitLabel(unit)}`}
      description="Your admin chooses another SME who has this subject. Until then the unit stays yours; what you save stays with the unit."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="reassign-form" busy={busy} disabled={!reason.trim()}>
            Send request
          </SubmitInDialog>
        </div>
      }
    >
      <form id="reassign-form" onSubmit={submit} className="space-y-4">
        <TextArea
          label="Reason"
          value={reason}
          required
          rows={3}
          maxLength={REASON_MAX}
          placeholder="e.g. on leave from next week"
          onChange={(e) => setReason(e.target.value)}
        />
        <FormError message={error} />
      </form>
    </Modal>
  )
}

/** The SME's own requests and what became of them. */
function MyRequests() {
  const requests = useDelegationRequests()
  if (!requests.data || requests.data.length === 0) return null
  return (
    <section aria-label="My reassignment requests" className="space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold tracking-wider text-gray-400 uppercase">
        <i className="fa-solid fa-inbox text-cyan-400" aria-hidden="true" />
        My reassignment requests
      </h3>
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
              {r.note && <p className="text-red-300">Admin's note: {r.note}</p>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
