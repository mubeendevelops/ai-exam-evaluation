import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { isApproved, isReviewable } from '../../components/evaluate/model'
import { useBookletDetail, useReview, type ReviewAnswer } from '../../components/evaluate/queries'
import { StatusBadge } from '../../components/evaluate/StatusBadge'
import { SmallButton } from '../../components/schema/controls'
import { Badge, EmptyState, GlassPanel, useToast, type Tone } from '../../components/ui'
import { formatMark, stageOf, type Stage } from '../../lib/review'
import { finalPath } from './StudentListPage'

const STAGE: Record<Stage, { label: string; tone: Tone }> = {
  todo: { label: 'Suggested', tone: 'amber' },
  skipped: { label: 'Skipped', tone: 'gray' },
  approved: { label: 'Approved', tone: 'emerald' },
  draft: { label: 'Amendment draft', tone: 'amber' },
  none: { label: 'Not attempted', tone: 'gray' },
}

/** The AI's answer flags, in the words of Panel B. */
export const FLAG: Record<string, string> = {
  low_ocr: 'Low OCR confidence',
  off_target: 'Off target',
  blank: 'Blank',
  mark_manually: 'Mark manually',
  duplicate: 'Answered twice',
  scorer_disagreement: 'Scorers disagree',
}

/** What the row says about the mark: the approved one, or the AI's suggestion. */
export function markText(a: ReviewAnswer): string {
  const max = formatMark(a.max_marks)
  if (!a.attempted) return 'Not attempted'
  if (a.status === 'approved' && a.approval)
    return `Approved: ${formatMark(a.approval.teacher_mark)} / ${max}`
  if (a.rescore_pending) return 'A new suggestion is on its way'
  const mark = a.suggestion?.mark
  if (mark === null || mark === undefined) {
    return a.suggestion?.flags.includes('mark_manually')
      ? `Mark manually (out of ${max})`
      : `No suggestion yet (out of ${max})`
  }
  return `AI suggested: ${formatMark(mark)} / ${max}`
}

const link =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700'

/**
 * A student's question list (D217, mock modal 2 as a page): every answer with its question,
 * state and mark (approved, or the AI's suggestion), from `GET /booklets/{id}/review`. An answer
 * opens step 3 at it; "Pages and segments" opens step 2. `?from=` carries the student list's
 * filter, so the way back lands on the same page of the list.
 */
export default function QuestionListPage() {
  const { unitId = '', bookletId = '' } = useParams()
  const [params] = useSearchParams()
  const from = params.get('from') ?? ''
  const detail = useBookletDetail(bookletId)
  const booklet = detail.data
  const reviewable = booklet !== undefined && isReviewable(booklet.status)
  const review = useReview(bookletId, reviewable, false)
  const back = `/sme/units/${unitId}${from}`
  const keep = from ? `&from=${encodeURIComponent(from)}` : ''

  if (detail.isError) {
    return (
      <EmptyState
        icon="fa-solid fa-file-circle-question"
        title="Booklet not found"
        action={
          <Link to={back} className={link}>
            Back to the students
          </Link>
        }
      >
        It is not in one of your units, or the unit has moved to another SME.
      </EmptyState>
    )
  }
  if (!booklet) return <p className="text-xs text-gray-400">Loading the booklet…</p>

  const answers = review.data?.answers ?? []
  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs text-gray-400">
        <Link to="/sme" className="hover:text-white">
          My Assignments
        </Link>{' '}
        ›{' '}
        <Link to={back} className="hover:text-white">
          {booklet.unit.code}
        </Link>{' '}
        › <span className="text-gray-200">{booklet.student.name}</span>
      </nav>
      <GlassPanel as="section" aria-labelledby="student-heading" className="space-y-3 p-5">
        <Link to={back} className="text-xs font-semibold text-purple-300 hover:text-purple-200">
          &larr; Back to Student Directory
        </Link>
        <div className="flex flex-col justify-between gap-3 md:flex-row md:items-start">
          <div>
            <h1 id="student-heading" className="text-lg font-bold text-white">
              {booklet.student.name}
            </h1>
            <p className="font-mono text-[11px] text-gray-400">
              Roll no. {booklet.student.usn} · {booklet.blueprint.title}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={booklet.status} />
            <Link to={`/evaluate?booklet=${booklet.id}${keep}`} className={link}>
              <i className="fa-solid fa-scissors" aria-hidden="true" /> Pages and segments
            </Link>
            {isApproved(booklet.status) && (
              <Link to={finalPath(booklet.id, from)} className={link}>
                View Final &rarr;
              </Link>
            )}
            {booklet.unit_state === 'open' && <DeleteBooklet bookletId={booklet.id} back={back} />}
          </div>
        </div>
      </GlassPanel>

      <GlassPanel as="section" aria-labelledby="questions-heading" className="space-y-3 p-5">
        <h2 id="questions-heading" className="text-base font-semibold text-white">
          Student Questions Overview
        </h2>
        {!reviewable && (
          <p className="text-xs text-gray-300">
            The AI is still reading this booklet: its questions appear once marks are suggested.
          </p>
        )}
        {review.isError && (
          <p role="alert" className="text-xs text-red-300">
            The questions could not be loaded.
          </p>
        )}
        {reviewable && review.data && answers.length === 0 && (
          <p className="text-xs text-gray-300">No answers were found in this booklet.</p>
        )}
        <ul className="space-y-2" aria-label="Questions">
          {answers.map((a) => {
            const stage = STAGE[stageOf(a)]
            const flags = a.suggestion?.flags.filter((f) => f !== 'mark_manually') ?? []
            const body = (
              <>
                <span className="flex min-w-0 items-center gap-3">
                  <span className="rounded-lg border border-gray-700 bg-gray-900 px-2 py-1 font-mono text-xs font-bold text-white">
                    Q{a.slot_label}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-mono text-[11px] text-gray-400">
                      {a.question_code ?? 'Question deleted'}
                    </span>
                    <span className="block text-xs text-gray-100">{markText(a)}</span>
                    {flags.length > 0 && (
                      <span className="mt-1 flex flex-wrap gap-1">
                        {flags.map((f) => (
                          <Badge key={f} tone="amber">
                            {FLAG[f] ?? f}
                          </Badge>
                        ))}
                      </span>
                    )}
                  </span>
                </span>
                <Badge tone={stage.tone}>{stage.label}</Badge>
              </>
            )
            const row =
              'flex items-center justify-between gap-3 rounded-xl border border-gray-800 bg-gray-900/40 p-3'
            return (
              <li key={a.id}>
                {a.attempted ? (
                  <Link
                    to={`/evaluate?booklet=${booklet.id}&step=3&answer=${encodeURIComponent(a.slot_label)}${keep}`}
                    aria-label={`Question ${a.slot_label}: ${stage.label}`}
                    className={`${row} hover:border-purple-500/50 hover:bg-gray-900/70`}
                  >
                    {body}
                  </Link>
                ) : (
                  <div className={`${row} opacity-60`}>{body}</div>
                )}
              </li>
            )
          })}
        </ul>
      </GlassPanel>
    </div>
  )
}

/** Delete the booklet (D14): images, text, marks and every sheet go; a content-free record
 * stays. Only while the unit is open (D206); asks first, in place (no browser dialog). */
function DeleteBooklet({ bookletId, back }: { bookletId: string; back: string }) {
  const toast = useToast()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const remove = useMutation({
    mutationFn: async () => {
      const { response, error } = await api.DELETE('/api/v1/booklets/{booklet_id}', {
        params: { path: { booklet_id: bookletId } },
      })
      if (!response.ok) throw new Error(problemOf(error, 'Could not delete the booklet.').message)
    },
    onSuccess: async () => {
      toast.show('The booklet was deleted. A record without content stays in the audit log.')
      await queryClient.invalidateQueries({ queryKey: ['unit'] })
      await queryClient.invalidateQueries({ queryKey: ['units'] })
      void navigate(back)
    },
    onError: (e) => {
      setConfirming(false)
      toast.show(e instanceof Error ? e.message : NETWORK_PROBLEM, 'red')
    },
  })
  if (!confirming) {
    return (
      <SmallButton tone="gray" icon="fa-solid fa-trash" onClick={() => setConfirming(true)}>
        Delete booklet
      </SmallButton>
    )
  }
  return (
    <span className="flex items-center gap-2 text-[11px] text-gray-200">
      Delete this booklet for good?
      <SmallButton tone="gray" disabled={remove.isPending} onClick={() => remove.mutate()}>
        Yes, delete
      </SmallButton>
      <SmallButton tone="gray" onClick={() => setConfirming(false)}>
        No
      </SmallButton>
    </span>
  )
}
