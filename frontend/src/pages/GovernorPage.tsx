import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { NETWORK_PROBLEM, problemOf } from '../api/errors'
import { useMe } from '../auth/AuthProvider'
import { Badge, EmptyState, GlassPanel, useToast } from '../components/ui'
import { ConfirmDialog, PrimaryButton } from './admin/controls'
import { UNIT_STATE, unitLabel, useUnits, type WorkUnit } from './admin/data'

/**
 * The Governor's page until the Governor portal is built (D159): the units SMEs have
 * submitted, each with "Freeze and sign off" (P28, D164). Freezing is final: nothing in the
 * unit changes again, and the frozen record keeps the marks for the reports (D168).
 */
export default function GovernorPage() {
  const me = useMe()
  const units = useUnits()
  const [freezing, setFreezing] = useState<WorkUnit | null>(null)
  const submitted = units.data?.filter((u) => u.state === 'submitted') ?? []
  const frozen = units.data?.filter((u) => u.state === 'frozen') ?? []
  return (
    <div className="space-y-6">
      <GlassPanel as="section" aria-labelledby="governor-title" className="space-y-3 p-6">
        <h1 id="governor-title" className="flex items-center gap-2 text-lg font-bold text-white">
          <i className="fa-solid fa-landmark text-purple-400" aria-hidden="true" />
          Governor portal
        </h1>
        <p className="text-sm text-gray-300">
          Welcome, {me.user.display_name}. You are signed in as the Governor of {me.college_name}.
        </p>
        <p className="text-sm text-gray-400">
          Units submitted by their SMEs wait here for your freeze and sign-off. Analytics, audits
          and reports come with the Governor portal.
        </p>
      </GlassPanel>
      {units.isError && (
        <p role="alert" className="text-xs text-red-300">
          The units could not be loaded.
        </p>
      )}
      {units.data && (
        <UnitList
          title="Submitted, waiting for sign-off"
          units={submitted}
          empty="No unit is waiting for your sign-off."
          onFreeze={setFreezing}
        />
      )}
      {frozen.length > 0 && <UnitList title="Frozen and signed off" units={frozen} />}
      {freezing && <FreezeDialog unit={freezing} onClose={() => setFreezing(null)} />}
    </div>
  )
}

function UnitList({
  title,
  units,
  empty,
  onFreeze,
}: {
  title: string
  units: WorkUnit[]
  empty?: string
  onFreeze?: (unit: WorkUnit) => void
}) {
  return (
    <GlassPanel as="section" aria-label={title} className="space-y-3 p-6">
      <h2 className="text-sm font-semibold tracking-wider text-gray-400 uppercase">{title}</h2>
      {units.length === 0 && empty && (
        <EmptyState icon="fa-solid fa-inbox" title={empty}>
          An SME submits a unit once every booklet is marked or has a reason.
        </EmptyState>
      )}
      <ul className="space-y-2">
        {units.map((u) => (
          <li
            key={u.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-800 p-3 text-xs"
          >
            <div className="space-y-1">
              <p className="font-semibold text-white">{unitLabel(u)}</p>
              <p className="text-gray-400">
                {u.paper.title} · {u.subject.name} · {u.owner.name} · {u.counts.completed} of{' '}
                {u.counts.total} booklets marked
                {u.submission && u.submission.unmarked.length > 0
                  ? ` · ${u.submission.unmarked.length} not marked (with a reason)`
                  : ''}
              </p>
              {u.state === 'submitted' && u.submission && (
                <p className="text-gray-400">
                  Submitted {new Date(u.submission.submitted_at).toLocaleString()}
                </p>
              )}
              {u.frozen && (
                <p className="text-gray-400">
                  Frozen by {u.frozen.frozen_by.name},{' '}
                  {new Date(u.frozen.frozen_at).toLocaleString()}
                </p>
              )}
            </div>
            {onFreeze ? (
              <PrimaryButton onClick={() => onFreeze(u)}>
                <i className="fa-solid fa-stamp" aria-hidden="true" /> Freeze and sign off
              </PrimaryButton>
            ) : (
              <Badge tone={UNIT_STATE[u.state].tone}>{UNIT_STATE[u.state].label}</Badge>
            )}
          </li>
        ))}
      </ul>
    </GlassPanel>
  )
}

function FreezeDialog({ unit, onClose }: { unit: WorkUnit; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)

  async function freeze() {
    if (busy) return
    setBusy(true)
    try {
      const { data, error } = await api.POST('/api/v1/units/{unit_id}/freeze', {
        params: { path: { unit_id: unit.id } },
        body: { expected_version: unit.version },
      })
      if (data) toast.show(`${unit.code} frozen and signed off.`)
      else toast.show(problemOf(error, 'Could not freeze the unit.').message, 'red')
      await queryClient.invalidateQueries({ queryKey: ['units'] })
      onClose()
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ConfirmDialog
      open
      title={`Freeze and sign off ${unit.code}?`}
      confirmLabel="Freeze and sign off"
      onConfirm={() => void freeze()}
      onClose={onClose}
    >
      {`This is final: nothing in ${unitLabel(unit)} can change afterwards, and the admin can no longer return it to the SME. The marks are kept in the frozen record for the reports.`}
    </ConfirmDialog>
  )
}
