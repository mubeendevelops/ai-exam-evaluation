import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError } from '../../components/public/fields'
import { SelectInput, TextArea } from '../../components/schema/controls'
import { Badge, EmptyState, GlassPanel, Modal, useToast } from '../../components/ui'
import { CancelButton, RowButton, SubmitInDialog, sectionTitle } from './controls'
import {
  REQUEST_STATE,
  useDelegationRequests,
  useUnits,
  type DelegationRequest,
  type DelegationState,
  type WorkUnit,
} from './data'
import { REASON_MAX, TransferDialog } from './TransferDialog'

type Dialog =
  | { kind: 'transfer'; unit: WorkUnit; reason?: string }
  | { kind: 'decline'; request: DelegationRequest }
  | null

function when(iso: string): string {
  return new Date(iso).toLocaleDateString()
}

/** Open units grouped by their SME, each with a Transfer action (the mock's steps 1 and 2). */
function UnitsBySme({ onTransfer }: { onTransfer: (unit: WorkUnit) => void }) {
  const units = useUnits({ state: 'open' })
  const groups = new Map<string, { name: string; units: WorkUnit[] }>()
  for (const unit of units.data ?? []) {
    const group = groups.get(unit.owner.id) ?? { name: unit.owner.name, units: [] }
    group.units.push(unit)
    groups.set(unit.owner.id, group)
  }
  const sorted = [...groups.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name))

  return (
    <GlassPanel as="section" aria-labelledby="by-sme-title" className="space-y-4 p-6">
      <h2 id="by-sme-title" className={sectionTitle}>
        <i className="fa-solid fa-layer-group text-amber-400" aria-hidden="true" /> Open units by
        SME
      </h2>
      {units.isPending && <p className="text-xs text-gray-400">Loading units…</p>}
      {units.isError && <FormError message="Could not load the units. Reload the page." />}
      {units.data && units.data.length === 0 && (
        <EmptyState icon="fa-solid fa-layer-group" title="No open units">
          Units appear here once you create them in Upload & Assign Papers.
        </EmptyState>
      )}
      {sorted.map(([ownerId, group]) => (
        <section key={ownerId} aria-label={`Units of ${group.name}`} className="space-y-2">
          <h3 className="text-sm font-semibold text-white">
            {group.name}{' '}
            <span className="text-xs font-normal text-gray-400">
              · {group.units.length} open unit{group.units.length === 1 ? '' : 's'}
            </span>
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">Open units of {group.name}</caption>
              <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
                <tr>
                  <th scope="col" className="pb-2 pr-4">
                    Unit
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Paper
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Subject
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Due
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Progress
                  </th>
                  <th scope="col" className="pb-2 text-right">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {group.units.map((unit) => (
                  <tr key={unit.id}>
                    <th scope="row" className="py-2 pr-4 font-semibold text-white">
                      {unit.code}
                      {unit.pending_request && (
                        <span className="ml-2">
                          <Badge tone="amber">reassignment asked</Badge>
                        </span>
                      )}
                    </th>
                    <td className="py-2 pr-4 text-gray-300">
                      {unit.paper.exam_code} · {unit.year}
                    </td>
                    <td className="py-2 pr-4 text-gray-300">{unit.subject.name}</td>
                    <td className="py-2 pr-4 text-gray-300">{unit.due_date}</td>
                    <td className="py-2 pr-4 text-gray-300">
                      {unit.counts.completed} of {unit.counts.total} completed
                    </td>
                    <td className="py-2 text-right">
                      <RowButton onClick={() => onTransfer(unit)}>Transfer</RowButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </GlassPanel>
  )
}

/** The SMEs' requests to move their units (the delegation inbox). */
function Inbox({
  onTransfer,
  onDecline,
}: {
  onTransfer: (request: DelegationRequest) => void
  onDecline: (request: DelegationRequest) => void
}) {
  const [state, setState] = useState<DelegationState | ''>('pending')
  const requests = useDelegationRequests(state || undefined)
  return (
    <GlassPanel as="section" aria-labelledby="inbox-title" className="space-y-4 p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="inbox-title" className={sectionTitle}>
          <i className="fa-solid fa-inbox text-cyan-400" aria-hidden="true" /> Delegation inbox
        </h2>
        <div className="w-48">
          <SelectInput
            label="Show"
            value={state}
            onChange={(e) => setState(e.target.value as DelegationState | '')}
          >
            <option value="pending">Pending</option>
            <option value="done">Transferred</option>
            <option value="declined">Declined</option>
            <option value="withdrawn">Withdrawn</option>
            <option value="">All requests</option>
          </SelectInput>
        </div>
      </div>
      {requests.isError && <FormError message="Could not load the requests. Reload the page." />}
      {requests.data && requests.data.length === 0 && (
        <p className="text-xs text-gray-400">
          {state === 'pending' ? 'No request is waiting.' : 'No requests here.'}
        </p>
      )}
      <ul className="space-y-3">
        {requests.data?.map((r) => {
          const tone = REQUEST_STATE[r.state]
          return (
            <li
              key={r.id}
              aria-label={`Request for ${r.unit.code}`}
              className="space-y-2 rounded-xl border border-gray-800 bg-gray-900/60 p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-white">
                  {r.unit.code} · {r.unit.paper_title} · {r.unit.subject.name}
                </p>
                <Badge tone={tone.tone}>{tone.label}</Badge>
              </div>
              <p className="text-xs text-gray-400">
                Asked by {r.requested_by.name} on {when(r.requested_at)} · now with{' '}
                {r.unit.owner.name} · {r.unit.counts.completed} of {r.unit.counts.total} completed
              </p>
              <p className="text-xs whitespace-pre-line text-gray-200">{r.reason}</p>
              {r.note && <p className="text-xs text-red-300">Your note: {r.note}</p>}
              {r.state === 'pending' && (
                <div className="flex gap-2">
                  <RowButton onClick={() => onTransfer(r)}>Transfer</RowButton>
                  <RowButton onClick={() => onDecline(r)}>Decline</RowButton>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </GlassPanel>
  )
}

function DeclineDialog({ request, onClose }: { request: DelegationRequest; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await api.POST(
        '/api/v1/delegation-requests/{request_id}/decline',
        { params: { path: { request_id: request.id } }, body: { note: note.trim() } },
      )
      if (data) {
        toast.show(`Request for ${request.unit.code} declined.`)
        await queryClient.invalidateQueries({ queryKey: ['delegation-requests'] })
        await queryClient.invalidateQueries({ queryKey: ['units'] })
        onClose()
      } else setError(problemOf(err, 'Could not decline the request.').message)
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
      eyebrow="Delegation inbox"
      title={`Decline the request for ${request.unit.code}`}
      description={`${request.requested_by.name} keeps the unit. They see your note.`}
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="decline-form" busy={busy} disabled={!note.trim()}>
            Decline request
          </SubmitInDialog>
        </div>
      }
    >
      <form id="decline-form" onSubmit={submit} className="space-y-4">
        <TextArea
          label="Note to the SME"
          value={note}
          required
          rows={3}
          maxLength={REASON_MAX}
          onChange={(e) => setNote(e.target.value)}
        />
        <FormError message={error} />
      </form>
    </Modal>
  )
}

/**
 * `/admin/redistribute` (P27, D163): open units by SME with a transfer dialog, and the
 * delegation inbox. Structure from the mock's "Redistribute Workload"; the look is the app's.
 */
export default function RedistributePage() {
  const units = useUnits({ state: 'open' })
  const [dialog, setDialog] = useState<Dialog>(null)
  const toast = useToast()

  function transferFromRequest(request: DelegationRequest) {
    const unit = units.data?.find((u) => u.id === request.unit.id)
    if (!unit) {
      toast.show('That unit is not open any more.', 'red')
      return
    }
    setDialog({ kind: 'transfer', unit, reason: request.reason })
  }

  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-arrow-right-arrow-left"
        iconTone="text-amber-400"
        title="Redistribute Workload"
        description="Move a unit, with every booklet as it stands, to another SME who has its subject; answer the SMEs' requests."
      />
      <Inbox
        onTransfer={transferFromRequest}
        onDecline={(request) => setDialog({ kind: 'decline', request })}
      />
      <UnitsBySme onTransfer={(unit) => setDialog({ kind: 'transfer', unit })} />
      {dialog?.kind === 'transfer' && (
        <TransferDialog
          unit={dialog.unit}
          initialReason={dialog.reason}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'decline' && (
        <DeclineDialog request={dialog.request} onClose={() => setDialog(null)} />
      )}
    </div>
  )
}
