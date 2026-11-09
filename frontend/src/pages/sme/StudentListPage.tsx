import { useEffect, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router'
import { DelegateDialog, PendingRequest } from '../../components/sme/DelegateDialog'
import { PAGE_SIZE, useUnitDetail, type RowStatus } from '../../components/sme/queries'
import { SubmitUnit } from '../../components/sme/SubmitDialog'
import { FAILURE, type Booklet } from '../../components/evaluate/model'
import { SelectInput, TextInput } from '../../components/schema/controls'
import { Badge, EmptyState, GlassPanel, type Tone } from '../../components/ui'
import { RowButton } from '../admin/controls'
import { UNIT_STATE, returnNote, type WorkUnit } from '../admin/data'
import { isOverdue, todayUtc } from './data'

export const ROW: Record<RowStatus, { label: string; tone: Tone }> = {
  processing: { label: 'Processing', tone: 'cyan' },
  needs_retake: { label: 'Needs retake', tone: 'amber' },
  failed: { label: 'Failed', tone: 'red' },
  ready: { label: 'Ready', tone: 'purple' },
  in_review: { label: 'In review', tone: 'indigo' },
  completed: { label: 'Completed', tone: 'emerald' },
}
const ROWS = Object.keys(ROW) as RowStatus[]

function statusOf(value: string | null): RowStatus | '' {
  return ROWS.find((r) => r === value) ?? ''
}

function pageOf(value: string | null): number {
  const n = Number(value)
  return Number.isInteger(n) && n > 1 ? n - 1 : 0
}

/** Where a booklet's question list is, carrying the list's filter for the way back. */
export function questionListPath(unitId: string, bookletId: string, from: string): string {
  const back = from ? `?from=${encodeURIComponent(from)}` : ''
  return `/sme/units/${unitId}/booklets/${bookletId}${back}`
}

/** The step 3 summary of a completed booklet: every result sheet version to download. */
export function finalPath(bookletId: string, from: string): string {
  const back = from ? `&from=${encodeURIComponent(from)}` : ''
  return `/evaluate?booklet=${bookletId}&step=3&view=summary${back}`
}

const link =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700'

/**
 * The student list of a unit (D217, mock modal 1 as a page): each student's booklet with its
 * status and approved answers, a filter by name or roll number and by status, 25 per page;
 * the filter and page live in the address (`?q=&status=&page=`), so Back and the question
 * list's back link return to the same view. One call: `GET /units/{id}` with the page asked.
 */
export default function StudentListPage() {
  const { unitId = '' } = useParams()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const status = statusOf(params.get('status'))
  const page = pageOf(params.get('page'))
  const [text, setText] = useState(q)
  const detail = useUnitDetail(unitId, { q, status, page })
  const [dialog, setDialog] = useState<'delegate' | 'submit' | null>(null)

  function update(change: { q?: string; status?: RowStatus | ''; page?: number }) {
    setParams(
      (old) => {
        const next = new URLSearchParams(old)
        for (const [key, value] of Object.entries(change)) {
          if (value === '' || value === 0 || value === undefined) next.delete(key)
          else next.set(key, key === 'page' ? String(Number(value) + 1) : String(value))
        }
        if (change.page === undefined) next.delete('page') // a new filter starts at page 1
        return next
      },
      { replace: true },
    )
  }

  // The box filters as you type, a moment after the last key.
  useEffect(() => {
    if (text.trim() === q.trim()) return
    const timer = setTimeout(() => update({ q: text.trim() }), 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `update` is rebuilt each render
  }, [text, q])

  if (detail.isError) {
    return (
      <EmptyState
        icon="fa-solid fa-layer-group"
        title="Unit not found"
        action={
          <Link to="/sme" className={link}>
            Back to My Assignments
          </Link>
        }
      >
        It is not one of your units, or it has been moved to another SME.
      </EmptyState>
    )
  }
  const unit = detail.data
  if (!unit) return <p className="text-xs text-gray-400">Loading the unit…</p>

  const first = unit.matching === 0 ? 0 : page * PAGE_SIZE + 1
  const last = page * PAGE_SIZE + unit.booklets.length
  const pages = Math.max(1, Math.ceil(unit.matching / PAGE_SIZE))
  const from = location.search

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs text-gray-400">
        <Link to="/sme" className="hover:text-white">
          My Assignments
        </Link>{' '}
        › <span className="text-gray-200">{unit.code}</span>
      </nav>
      <UnitHeader
        unit={unit}
        onDelegate={() => setDialog('delegate')}
        onSubmit={() => setDialog('submit')}
      />
      {dialog === 'delegate' && <DelegateDialog unit={unit} onClose={() => setDialog(null)} />}
      {dialog === 'submit' && <SubmitUnit unit={unit} onClose={() => setDialog(null)} />}

      <GlassPanel as="section" aria-labelledby="students-heading" className="space-y-4 p-5">
        <div className="flex flex-col justify-between gap-3 md:flex-row md:items-end">
          <div>
            <h2 id="students-heading" className="text-base font-semibold text-white">
              Student Answer Sheets Directory
            </h2>
            <p className="text-xs text-gray-400">
              Select a student to review the questions and the AI's suggested marks.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 md:w-[30rem]">
            <TextInput
              label="Search student name or roll no."
              type="search"
              value={text}
              maxLength={80}
              onChange={(e) => setText(e.target.value)}
            />
            <SelectInput
              label="Status"
              value={status}
              onChange={(e) => update({ status: statusOf(e.target.value) })}
            >
              <option value="">All statuses</option>
              {ROWS.map((r) => (
                <option key={r} value={r}>
                  {ROW[r].label}
                </option>
              ))}
            </SelectInput>
          </div>
        </div>

        {unit.counts.total === 0 ? (
          <EmptyState icon="fa-solid fa-file-circle-plus" title="No booklets yet">
            Booklets of this unit appear here once your admin uploads them.
          </EmptyState>
        ) : unit.matching === 0 ? (
          <EmptyState
            icon="fa-solid fa-filter-circle-xmark"
            title="No student matches"
            action={
              <RowButton
                onClick={() => {
                  setText('')
                  update({ q: '', status: '' })
                }}
              >
                Clear the filter
              </RowButton>
            }
          />
        ) : (
          <StudentTable unitId={unit.id} booklets={unit.booklets} from={from} />
        )}

        {unit.matching > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-gray-300">
            <span>
              Showing {first}–{last} of {unit.matching} students
            </span>
            <div className="flex items-center gap-2">
              <RowButton disabled={page === 0} onClick={() => update({ page: page - 1 })}>
                Prev
              </RowButton>
              <span aria-live="polite">
                Page {page + 1} of {pages}
              </span>
              <RowButton disabled={page + 1 >= pages} onClick={() => update({ page: page + 1 })}>
                Next
              </RowButton>
            </div>
          </div>
        )}
      </GlassPanel>
    </div>
  )
}

function UnitHeader({
  unit,
  onDelegate,
  onSubmit,
}: {
  unit: WorkUnit
  onDelegate: () => void
  onSubmit: () => void
}) {
  const note = returnNote(unit)
  return (
    <GlassPanel as="section" aria-labelledby="unit-heading" className="space-y-3 p-5">
      <div className="flex flex-col justify-between gap-3 md:flex-row md:items-start">
        <div className="space-y-1">
          <h1 id="unit-heading" className="text-lg font-bold text-white">
            {unit.paper.title}
          </h1>
          <p className="font-mono text-[11px] text-gray-400">
            {unit.code} · {unit.paper.exam_code} · {unit.year}
          </p>
          <p className="flex flex-wrap items-center gap-2 text-xs text-gray-300">
            <Badge tone="purple">{unit.subject.name}</Badge>
            <span>Due {unit.due_date}</span>
            {isOverdue(unit, todayUtc()) && <Badge tone="red">Overdue</Badge>}
            <Badge tone={UNIT_STATE[unit.state].tone}>{UNIT_STATE[unit.state].label}</Badge>
            <span>
              {unit.counts.completed} of {unit.counts.total} completed
            </span>
          </p>
        </div>
        {unit.state === 'open' && (
          <div className="flex flex-wrap items-center gap-2">
            {unit.pending_request ? (
              <PendingRequest unit={unit} />
            ) : (
              <RowButton onClick={onDelegate}>Delegate</RowButton>
            )}
            <RowButton disabled={unit.counts.total === 0} onClick={onSubmit}>
              Submit unit
            </RowButton>
          </div>
        )}
        {unit.state !== 'open' && (
          <Badge tone={UNIT_STATE[unit.state].tone}>
            {unit.state === 'submitted' ? 'Submitted to your admin' : 'Frozen and signed off'}
          </Badge>
        )}
      </div>
      {note && (
        <p
          role="note"
          className="rounded-xl border border-amber-500/30 bg-amber-950/30 px-4 py-2 text-xs text-amber-100"
        >
          <i className="fa-solid fa-rotate-left mr-1.5 text-amber-300" aria-hidden="true" />
          Your admin returned this unit: {note}
        </p>
      )}
    </GlassPanel>
  )
}

function StudentTable({
  unitId,
  booklets,
  from,
}: {
  unitId: string
  booklets: Booklet[]
  from: string
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Students of the unit</caption>
        <thead className="text-[11px] tracking-wider text-gray-400 uppercase">
          <tr>
            <th scope="col" className="px-3 py-2">
              Student
            </th>
            <th scope="col" className="px-3 py-2">
              Roll no.
            </th>
            <th scope="col" className="px-3 py-2">
              Status
            </th>
            <th scope="col" className="px-3 py-2">
              Approved
            </th>
            <th scope="col" className="px-3 py-2">
              <span className="sr-only">Action</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800">
          {booklets.map((b) => {
            const row = ROW[b.row_status]
            return (
              <tr key={b.id}>
                <th scope="row" className="px-3 py-3 font-semibold text-white">
                  {b.student.name}
                </th>
                <td className="px-3 py-3 font-mono text-gray-300">{b.student.usn}</td>
                <td className="px-3 py-3">
                  <Badge tone={row.tone}>{row.label}</Badge>
                </td>
                <td className="px-3 py-3 text-gray-200">
                  {b.answers && b.answers.attempted > 0
                    ? `${b.answers.approved} of ${b.answers.attempted}`
                    : '—'}
                </td>
                <td className="px-3 py-3 text-right">
                  <RowAction unitId={unitId} booklet={b} from={from} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function RowAction({ unitId, booklet, from }: { unitId: string; booklet: Booklet; from: string }) {
  const who = booklet.student.name
  switch (booklet.row_status) {
    case 'completed':
      return (
        <Link to={finalPath(booklet.id, from)} className={link} aria-label={`View final of ${who}`}>
          View Final &rarr;
        </Link>
      )
    case 'ready':
    case 'in_review':
      return (
        <Link
          to={questionListPath(unitId, booklet.id, from)}
          className={link}
          aria-label={`Review questions of ${who}`}
        >
          Review questions &rarr;
        </Link>
      )
    case 'needs_retake':
      return <span className="text-[11px] text-amber-300">Waiting for a retake by your admin</span>
    case 'failed':
      return (
        <span className="text-[11px] text-red-300">
          {booklet.failure_reason ? FAILURE[booklet.failure_reason] : 'Processing failed.'}
        </span>
      )
    default:
      return (
        <span className="inline-flex flex-wrap items-center justify-end gap-2">
          <span className="text-[11px] text-gray-400">Being read by the AI…</span>
          <Link
            to={questionListPath(unitId, booklet.id, from)}
            className={link}
            aria-label={`Open the booklet of ${who}`}
          >
            Open
          </Link>
        </span>
      )
  }
}
