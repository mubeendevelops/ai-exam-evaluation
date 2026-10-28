import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { CancelButton, RowButton, SubmitInDialog } from '../../pages/admin/controls'
import {
  REQUEST_STATE,
  UNIT_STATE,
  returnNote,
  unitLabel,
  useDelegationRequests,
  useUnits,
  type WorkUnit,
} from '../../pages/admin/data'
import { REASON_MAX } from '../../pages/admin/TransferDialog'
import { FormError } from '../public/fields'
import { SelectInput, TextArea } from '../schema/controls'
import { Badge, EmptyState, GlassPanel, Modal, useToast } from '../ui'
import { STATUS, type Booklet } from './model'
import { SubmissionCard } from './SubmissionCard'

/**
 * Step 1: the booklets of the SME's units (P26). The admin uploads them into a unit (D162);
 * the SME picks a unit, sees each student's booklet and its status, and opens one. When every
 * booklet is marked (or has a reason) the SME submits the unit to the admin (P28, D164).
 */
export function UploadStep({ onOpen }: { onOpen: (bookletId: string) => void }) {
  const units = useUnits()
  const [picked, setPicked] = useState('')
  const unitId = picked || units.data?.[0]?.id || ''
  const unit = units.data?.find((u) => u.id === unitId)
  const [asking, setAsking] = useState(false)
  const [submitting, setSubmitting] = useState(false)

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
                {u.state === 'open'
                  ? unitLabel(u)
                  : `${unitLabel(u)} · ${UNIT_STATE[u.state].label}`}
              </option>
            ))}
          </SelectInput>
          {unit && unit.state === 'open' && (
            <div className="flex flex-wrap items-end gap-2">
              <Reassignment unit={unit} onAsk={() => setAsking(true)} />
              <RowButton
                disabled={!list || list.items.length === 0}
                onClick={() => setSubmitting(true)}
              >
                Submit unit
              </RowButton>
            </div>
          )}
          {unit && unit.state !== 'open' && (
            <div className="flex items-end">
              <Badge tone={UNIT_STATE[unit.state].tone}>
                {unit.state === 'submitted' ? 'Submitted to your admin' : 'Frozen and signed off'}
              </Badge>
            </div>
          )}
          {unit && returnNote(unit) && (
            <p
              role="note"
              className="rounded-xl border border-amber-500/30 bg-amber-950/30 px-4 py-2 text-xs text-amber-100 md:col-span-2"
            >
              <i className="fa-solid fa-rotate-left mr-1.5 text-amber-300" aria-hidden="true" />
              Your admin returned this unit: {returnNote(unit)}
            </p>
          )}
        </GlassPanel>
      )}
      {asking && unit && <RequestDialog unit={unit} onClose={() => setAsking(false)} />}
      {submitting && unit && list && (
        <SubmitDialog unit={unit} booklets={list.items} onClose={() => setSubmitting(false)} />
      )}

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
function SubmitDialog({
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
          queryClient.invalidateQueries({ queryKey: ['booklets'] }),
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
