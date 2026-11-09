import { useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { RequestDialog, SubmitDialog, useUnitBooklets } from '../../components/evaluate/UploadStep'
import { SelectInput } from '../../components/schema/controls'
import { Badge, EmptyState, GlassPanel } from '../../components/ui'
import { RowButton } from '../admin/controls'
import { UNIT_STATE, percent, returnNote, useUnits, type WorkUnit } from '../admin/data'
import {
  SORTS,
  firstOpenUnit,
  isOverdue,
  sortOf,
  sortUnits,
  todayUtc,
  useAssignmentSummary,
  type AssignmentSummary,
  type Sort,
} from './data'

type StateFilter = '' | WorkUnit['state']
const FILTERS: { value: StateFilter; label: string }[] = [
  { value: '', label: 'All states' },
  { value: 'open', label: 'Open' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'frozen', label: 'Frozen' },
]

function filterOf(value: string | null): StateFilter {
  return FILTERS.find((f) => f.value === value)?.value ?? ''
}

const action =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700'

/**
 * My Assignments (P31, D216): the SME's landing page. Two cards of figures (the server counts
 * them), the units table from `GET /units` with sort and state filter kept in the address, and
 * the mock's guidelines card without its imagery. Evaluate opens step 1 of `/evaluate` with the
 * unit picked; Delegate and Submit are the existing dialogs (P32 replaces them).
 */
export default function MyAssignmentsPage() {
  const [params, setParams] = useSearchParams()
  const sort = sortOf(params.get('sort'))
  const state = filterOf(params.get('state'))
  const units = useUnits()
  const summary = useAssignmentSummary()
  const table = useRef<HTMLDivElement>(null)

  function update(change: { sort?: Sort; state?: StateFilter }) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(change)) {
      const fallback = key === 'sort' ? 'due-asc' : ''
      if (value === fallback) next.delete(key)
      else next.set(key, value)
    }
    setParams(next, { replace: true })
  }

  function showActive() {
    update({ state: 'open' })
    table.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }

  const all = units.data ?? []
  const shown = sortUnits(
    all.filter((u) => !state || u.state === state),
    sort,
  )
  const start = firstOpenUnit(all)

  return (
    <div className="space-y-6">
      <h1 className="sr-only">My Assignments</h1>
      {summary.isError && (
        <p role="alert" className="text-xs text-red-300">
          Your figures could not be loaded.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <TotalCard summary={summary.data} onActive={showActive} />
        <HistoryCard summary={summary.data} />
      </div>

      <div ref={table} className="scroll-mt-20">
        <GlassPanel as="section" aria-labelledby="units-heading" className="space-y-4 p-5">
          <div className="flex flex-col justify-between gap-3 md:flex-row md:items-end">
            <div>
              <h2 id="units-heading" className="text-base font-semibold text-white">
                Assignments Repository
              </h2>
              <p className="text-xs text-gray-400">
                Evaluate, submit or ask to delegate the units assigned to you.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3 md:w-[28rem]">
              <SelectInput
                label="Order by"
                value={sort}
                onChange={(e) => update({ sort: sortOf(e.target.value) })}
              >
                {SORTS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </SelectInput>
              <SelectInput
                label="State"
                value={state}
                onChange={(e) => update({ state: filterOf(e.target.value) })}
              >
                {FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </SelectInput>
            </div>
          </div>

          {units.isError && (
            <p role="alert" className="text-xs text-red-300">
              Your units could not be loaded.
            </p>
          )}
          {units.data && all.length === 0 && (
            <EmptyState icon="fa-solid fa-layer-group" title="No units assigned to you">
              Your admin assigns units of work to you; you are emailed when they do.
            </EmptyState>
          )}
          {all.length > 0 && shown.length === 0 && (
            <EmptyState
              icon="fa-solid fa-filter-circle-xmark"
              title="No units in this state"
              action={<RowButton onClick={() => update({ state: '' })}>Show all units</RowButton>}
            />
          )}
          {shown.length > 0 && <UnitsTable units={shown} />}
        </GlassPanel>
      </div>

      <GlassPanel as="section" aria-labelledby="guidelines-heading" className="space-y-3 p-5">
        <h2 id="guidelines-heading" className="text-base font-semibold text-white">
          AI-assisted evaluation and question schema blueprinting
        </h2>
        <p className="text-xs text-gray-300">
          The AI suggests a mark for each answer and shows its reasons; you decide every mark. When
          every booklet of a unit is approved (or has a reason it is not), submit the unit to your
          admin. Requests to delegate a unit go to your admin, who chooses the SME.
        </p>
        <div className="flex flex-wrap gap-2">
          {start ? (
            <Link to={`/evaluate?unit=${start.id}`} className={action}>
              Start evaluating {start.code} &rarr;
            </Link>
          ) : (
            <span className={`${action} cursor-not-allowed opacity-50`} aria-disabled="true">
              No open unit to evaluate
            </span>
          )}
          <Link to="/sme/schema" className={action}>
            Manage Schema
          </Link>
        </div>
      </GlassPanel>
    </div>
  )
}

function Figure({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-3">
      <p className="text-[11px] text-gray-400">{label}</p>
      <p className="text-xl font-bold text-white">{value}</p>
    </div>
  )
}

function TotalCard({
  summary,
  onActive,
}: {
  summary: AssignmentSummary | undefined
  onActive: () => void
}) {
  return (
    <GlassPanel as="section" aria-labelledby="total-heading" className="space-y-4 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 id="total-heading" className="text-sm font-semibold text-gray-300">
          Total Assigned
        </h2>
        <Badge tone="gray">Lifetime: {summary?.units_lifetime ?? '…'}</Badge>
      </div>
      <p>
        <span className="text-3xl font-bold text-white">{summary?.units_current_year ?? '…'}</span>{' '}
        <span className="text-xs text-gray-400">
          {summary ? `assignments in academic year ${summary.academic_year.label}` : ''}
        </span>
      </p>
      <div className="grid grid-cols-2 gap-3">
        <Figure label="Completed" value={summary?.completed ?? '…'} />
        <button
          type="button"
          onClick={onActive}
          className="rounded-xl border border-cyan-500/30 bg-cyan-950/30 p-3 text-left hover:bg-cyan-950/50"
        >
          <span className="block text-[11px] text-cyan-300">Active</span>
          <span className="block text-xl font-bold text-cyan-300">{summary?.active ?? '…'}</span>
          <span className="block text-[10px] text-cyan-300/80 underline">
            Show open units &rarr;
          </span>
        </button>
      </div>
    </GlassPanel>
  )
}

const BAR = [
  { key: 'frozen', label: 'Frozen', className: 'bg-emerald-500', dot: 'bg-emerald-400' },
  { key: 'submitted', label: 'Submitted', className: 'bg-amber-500', dot: 'bg-amber-400' },
  { key: 'open', label: 'Open', className: 'bg-cyan-500', dot: 'bg-cyan-400' },
] as const

function HistoryCard({ summary }: { summary: AssignmentSummary | undefined }) {
  const total = summary?.units_lifetime ?? 0
  const rate = summary?.completion_rate
  const days = summary?.average_turnaround_days
  const parts = BAR.map((b) => ({ ...b, count: summary?.breakdown[b.key] ?? 0 }))
  return (
    <GlassPanel as="section" aria-labelledby="history-heading" className="space-y-4 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 id="history-heading" className="text-sm font-semibold text-gray-300">
          Historical (all assignments)
        </h2>
        <Badge tone="emerald">
          {rate === null || rate === undefined ? '—' : `${rate.toFixed(1)}%`} completion rate
        </Badge>
      </div>
      <p className="text-xs text-gray-400">
        {summary ? `${summary.completed} / ${total} completed` : '…'}
      </p>
      <div
        role="img"
        aria-label={parts.map((p) => `${p.label}: ${p.count}`).join(', ')}
        className="flex h-2.5 overflow-hidden rounded-full bg-gray-800"
      >
        {total > 0 &&
          parts.map((p) => (
            <div
              key={p.key}
              className={p.className}
              style={{ width: `${(p.count / total) * 100}%` }}
              title={`${p.label}: ${p.count} (${percent(p.count, total)}%)`}
            />
          ))}
      </div>
      <ul className="flex flex-wrap gap-4 text-[11px] text-gray-300" aria-hidden="true">
        {parts.map((p) => (
          <li key={p.key} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${p.dot}`} /> {p.label}: {p.count}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-800 pt-3 text-xs text-gray-300">
        <span>
          Average turnaround:{' '}
          <strong className="text-white">
            {days === null || days === undefined ? '—' : `${days.toFixed(1)} days`}
          </strong>
        </span>
        <Link to="/sme/reports" className="font-semibold text-purple-300 hover:text-purple-200">
          Full analytics &rarr;
        </Link>
      </div>
    </GlassPanel>
  )
}

function UnitsTable({ units }: { units: WorkUnit[] }) {
  const today = todayUtc()
  const [asking, setAsking] = useState<WorkUnit | null>(null)
  const [submitting, setSubmitting] = useState<WorkUnit | null>(null)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">My units</caption>
        <thead className="text-[11px] tracking-wider text-gray-400 uppercase">
          <tr>
            <th scope="col" className="px-3 py-2">
              Assignment
            </th>
            <th scope="col" className="px-3 py-2">
              Subject
            </th>
            <th scope="col" className="px-3 py-2">
              Due date
            </th>
            <th scope="col" className="px-3 py-2">
              Completion
            </th>
            <th scope="col" className="px-3 py-2">
              State
            </th>
            <th scope="col" className="px-3 py-2">
              Actions
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800">
          {units.map((u) => {
            const share = percent(u.counts.completed, u.counts.total)
            const note = returnNote(u)
            return (
              <tr key={u.id} className="align-top">
                <th scope="row" className="px-3 py-3 font-normal">
                  <span className="block font-semibold text-white">{u.paper.title}</span>
                  <span className="font-mono text-[11px] text-gray-400">
                    {u.code} · {u.paper.exam_code ?? '—'} · {u.year}
                  </span>
                </th>
                <td className="px-3 py-3">
                  <Badge tone="purple">{u.subject.name}</Badge>
                </td>
                <td className="px-3 py-3 whitespace-nowrap text-gray-200">
                  {u.due_date}
                  {isOverdue(u, today) && (
                    <Badge tone="red" className="ml-2">
                      Overdue
                    </Badge>
                  )}
                </td>
                <td className="min-w-32 px-3 py-3">
                  <div className="mb-1 flex justify-between text-[11px] text-gray-300">
                    <span>
                      {u.counts.completed}/{u.counts.total}
                    </span>
                    <span>{share}%</span>
                  </div>
                  <div
                    role="progressbar"
                    aria-label={`${u.code} completion`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={share}
                    className="h-1.5 overflow-hidden rounded-full bg-gray-800"
                  >
                    <div className="h-full bg-cyan-500" style={{ width: `${share}%` }} />
                  </div>
                </td>
                <td className="px-3 py-3">
                  <Badge tone={UNIT_STATE[u.state].tone}>{UNIT_STATE[u.state].label}</Badge>
                  {note && (
                    <Badge tone="amber" className="mt-1">
                      <span title={note}>Returned</span>
                    </Badge>
                  )}
                </td>
                <td className="px-3 py-3">
                  <div className="flex flex-wrap gap-1.5">
                    <Link
                      to={`/evaluate?unit=${u.id}`}
                      className={action}
                      aria-label={`Evaluate ${u.code}`}
                    >
                      <i className="fa-solid fa-circle-check" aria-hidden="true" /> Evaluate
                    </Link>
                    {u.state === 'open' &&
                      (u.pending_request ? (
                        <Badge tone="amber">Reassignment requested</Badge>
                      ) : (
                        <RowButton aria-label={`Delegate ${u.code}`} onClick={() => setAsking(u)}>
                          Delegate
                        </RowButton>
                      ))}
                    {u.state === 'open' && (
                      <RowButton
                        aria-label={`Submit ${u.code}`}
                        disabled={u.counts.total === 0}
                        onClick={() => setSubmitting(u)}
                      >
                        Submit
                      </RowButton>
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {asking && <RequestDialog unit={asking} onClose={() => setAsking(null)} />}
      {submitting && <SubmitUnit unit={submitting} onClose={() => setSubmitting(null)} />}
    </div>
  )
}

/** Submit needs the unit's booklets (which to give a reason for): load them, then the dialog. */
function SubmitUnit({ unit, onClose }: { unit: WorkUnit; onClose: () => void }) {
  const booklets = useUnitBooklets(unit.id)
  if (booklets.isError) {
    return (
      <p role="alert" className="mt-2 text-xs text-red-300">
        The booklets of {unit.code} could not be loaded.
      </p>
    )
  }
  if (!booklets.data) return null
  return <SubmitDialog unit={unit} booklets={booklets.data.items} onClose={onClose} />
}
