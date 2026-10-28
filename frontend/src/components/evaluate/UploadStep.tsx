import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { unitLabel, useUnits } from '../../pages/admin/data'
import { SelectInput } from '../schema/controls'
import { EmptyState, GlassPanel } from '../ui'
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
        </GlassPanel>
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
    </div>
  )
}
