import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { api } from '../../api/client'
import {
  neighbour,
  nextOpen,
  parseComparison,
  progressOf,
  stageOf,
  type Stage,
} from '../../lib/review'
import { asRect, buildSegments, indexRegions } from '../../lib/segments'
import { SmallButton } from '../schema/controls'
import { Badge, EmptyState, GlassPanel, useToast, type Tone } from '../ui'
import { AnswerMappingPanel, type OverlayInput } from './AnswerMappingPanel'
import { BookletSummary } from './BookletSummary'
import { DiagnosticPanel } from './DiagnosticPanel'
import { GradingPanel, type Decision } from './GradingPanel'
import { FAILURE, isReviewable } from './model'
import {
  reviewKey,
  useBookletDetail,
  useBookletLock,
  useComparisons,
  useExamSlots,
  usePageTexts,
  useQuestion,
  useReview,
  useSegments,
  useStudentDiagrams,
  type Review,
  type ReviewAnswer,
} from './queries'
import { StatusBadge } from './StatusBadge'
import { useGuardedEdit } from './useEdits'

const CHIP: Record<Stage, { label: string; tone: Tone; dot: string }> = {
  todo: { label: 'to approve', tone: 'cyan', dot: 'border-cyan-500/40 text-cyan-200' },
  skipped: { label: 'skipped', tone: 'gray', dot: 'border-gray-500 text-gray-300 border-dashed' },
  approved: {
    label: 'approved',
    tone: 'emerald',
    dot: 'border-emerald-500/50 bg-emerald-950/60 text-emerald-300',
  },
  draft: {
    label: 'amendment draft',
    tone: 'amber',
    dot: 'border-amber-500/50 bg-amber-950/60 text-amber-200',
  },
  none: { label: 'not attempted', tone: 'gray', dot: 'border-gray-800 text-gray-500' },
}

/**
 * Step 3, "Evaluation View": one answer at a time in three panels (the mapping to the key, the
 * AI's reasoning, the teacher's decision), a strip to move between answers, and the booklet
 * summary where the booklet is approved and amendments start. The AI suggests, the teacher
 * decides: every decision is one API call under the booklet lock and names the version it was
 * based on. The address keeps the place: `?booklet=<id>&step=3[&answer=<label>][&view=summary]`.
 */
export function EvaluationStep({
  bookletId,
  onBack,
  onSubmissions,
}: {
  bookletId: string
  onBack: () => void
  onSubmissions: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const detail = useBookletDetail(bookletId)
  const booklet = detail.data
  const status = booklet?.status
  const reviewable = status !== undefined && isReviewable(status)

  const { lock, reopen } = useBookletLock(bookletId, reviewable)
  const review = useReview(bookletId, reviewable, false)
  const segments = useSegments(bookletId, reviewable)
  const { texts } = usePageTexts(booklet)
  const diagrams = useStudentDiagrams(bookletId, reviewable)
  const slots = useExamSlots(booklet?.blueprint.id)
  const guarded = useGuardedEdit(bookletId, reopen)
  const [busy, setBusy] = useState(false)

  const data = review.data
  const answers = useMemo(() => data?.answers ?? [], [data])
  const summary = params.get('view') === 'summary'
  const wanted = params.get('answer')
  const current: ReviewAnswer | undefined =
    answers.find((a) => a.attempted && a.slot_label === wanted) ??
    nextOpen(answers, null) ??
    answers.find((a) => a.attempted)
  const label = current?.slot_label ?? null

  const go = (patch: { answer?: string | null; summary?: boolean }) =>
    setParams(
      (old) => {
        const next = new URLSearchParams(old)
        if (patch.answer !== undefined) {
          if (patch.answer === null) next.delete('answer')
          else next.set('answer', patch.answer)
        }
        if (patch.summary !== undefined) {
          if (patch.summary) next.set('view', 'summary')
          else next.delete('view')
        }
        return next
      },
      { replace: true },
    )

  const views = useMemo(
    () => buildSegments(segments.data?.segments ?? [], texts),
    [segments.data, texts],
  )
  const ofAnswer = views.filter((v) => v.label !== null && v.label === label)
  const confidences = ofAnswer.flatMap((v) => (v.confidence === null ? [] : [v.confidence]))
  const ocrConfidence = confidences.length
    ? confidences.reduce((a, b) => a + b, 0) / confidences.length
    : null

  const slot = label ? slots.data?.get(label) : undefined
  const question = useQuestion(slot?.questionId)
  const comparisons = useComparisons(bookletId, current?.id, current?.suggestion?.id)

  const overlays: OverlayInput[] = (comparisons.data ?? []).flatMap((c, k) => {
    const comparison = parseComparison(c.document)
    if (!comparison) return []
    const drawing = (diagrams.data ?? []).find((d) => d.id === comparison.student_diagram_id)
    const regions = indexRegions(texts)
    const pageNumber =
      (drawing?.region_id ? regions.get(drawing.region_id)?.page : undefined) ??
      ofAnswer.find((v) => v.segment.id === drawing?.segment_id)?.pages[0]
    const page = booklet?.pages.find((p) => p.number === pageNumber)
    return [
      {
        key: `${comparison.student_diagram_id ?? 'none'}-${k}`,
        comparison,
        drawing:
          drawing && page?.image_url
            ? {
                graph: drawing.graph,
                imagePath: page.image_url,
                imageSize: { width: page.width, height: page.height },
                crop: asRect(drawing.box),
              }
            : undefined,
      },
    ]
  })

  const canEdit = lock.kind === 'mine'
  const holder = lock.kind === 'other' ? lock : null
  const readOnlyReason = holder
    ? `${holder.holder ?? 'Another teacher'} has this booklet open${holder.until ? ` until ${new Date(holder.until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}. You can look, but not decide anything.`
    : lock.kind === 'error'
      ? lock.message
      : lock.kind === 'idle'
        ? 'Opening the booklet for review…'
        : undefined

  const act = async (work: () => Promise<void>) => {
    setBusy(true)
    try {
      await work()
    } finally {
      setBusy(false)
    }
  }

  /** One decision: the reply is the whole review, which replaces the cached one at once. */
  const decide = async (
    send: () => Promise<{ data?: Review; error?: unknown; response: Response }>,
    failure: string,
  ): Promise<Review | undefined> => {
    const done = await guarded.run(send, failure)
    if (done) queryClient.setQueryData(reviewKey(bookletId), done)
    return done
  }

  const path = (answer: ReviewAnswer) => ({
    params: { path: { booklet_id: bookletId, answer_id: answer.id } },
  })

  const approveAnswer = (a: ReviewAnswer, d: Decision) =>
    act(async () => {
      const done = await decide(
        () =>
          api.POST('/api/v1/booklets/{booklet_id}/answers/{answer_id}/approve', {
            ...path(a),
            body: {
              expected_version: a.version,
              teacher_mark: d.teacherMark,
              tags: d.tags,
              remarks: d.remarks,
            },
          }),
        'Could not approve the answer.',
      )
      if (!done) return
      const next = nextOpen(done.answers, a.slot_label)
      if (next) {
        toast.show(`Question ${a.slot_label} approved.`)
        go({ answer: next.slot_label })
      } else {
        toast.show(
          done.approved
            ? `Question ${a.slot_label} approved. The amendment is complete: see the booklet summary.`
            : `Question ${a.slot_label} approved. That was the last one: check the summary and approve the booklet.`,
        )
        go({ answer: a.slot_label, summary: true })
      }
    })

  const skipAnswer = (a: ReviewAnswer) =>
    act(async () => {
      const done = await decide(
        () =>
          api.POST('/api/v1/booklets/{booklet_id}/answers/{answer_id}/skip', {
            ...path(a),
            body: { expected_version: a.version },
          }),
        'Could not skip the answer.',
      )
      if (!done) return
      const next = nextOpen(done.answers, a.slot_label)
      toast.show(
        next
          ? `Question ${a.slot_label} skipped. It stays in the list for later.`
          : `Question ${a.slot_label} skipped. Nothing else is waiting: come back to it when you are ready.`,
      )
      if (next) go({ answer: next.slot_label })
    })

  const reopenAnswer = (a: ReviewAnswer, reason: string, thenOpen: boolean) =>
    act(async () => {
      const done = await decide(
        () =>
          api.POST('/api/v1/booklets/{booklet_id}/answers/{answer_id}/reopen', {
            ...path(a),
            body: { expected_version: a.version, reason: reason.trim() },
          }),
        'Could not reopen the answer.',
      )
      if (!done) return
      toast.show(
        done.amendment_in_progress
          ? `Question ${a.slot_label} is open for amendment. The issued result sheet stays valid.`
          : `Question ${a.slot_label} reopened.`,
      )
      if (thenOpen) go({ answer: a.slot_label, summary: false })
    })

  const withdrawDraft = (a: ReviewAnswer) =>
    act(async () => {
      const done = await decide(
        () =>
          api.POST('/api/v1/booklets/{booklet_id}/answers/{answer_id}/withdraw', {
            ...path(a),
            body: { expected_version: a.version },
          }),
        'Could not withdraw the amendment.',
      )
      if (done)
        toast.show(`Amendment of question ${a.slot_label} withdrawn: the earlier approval stands.`)
    })

  const approveBooklet = () =>
    act(async () => {
      const done = await decide(
        () =>
          api.POST('/api/v1/booklets/{booklet_id}/approve', {
            params: { path: { booklet_id: bookletId } },
            body: { expected_version: guarded.version() },
          }),
        'Could not approve the booklet.',
      )
      const sheet = done?.sheets[done.sheets.length - 1]
      if (done) {
        toast.show(`Booklet approved.${sheet ? ` Result sheet v${sheet.version} is stored.` : ''}`)
      }
    })

  if (detail.isError) {
    return (
      <EmptyState
        icon="fa-solid fa-file-circle-question"
        title="This booklet could not be opened"
        action={
          <SmallButton icon="fa-solid fa-arrow-left" onClick={onSubmissions}>
            Back to the submissions
          </SmallButton>
        }
      >
        It may have been deleted, or it belongs to another college.
      </EmptyState>
    )
  }
  if (!booklet || !status) return <p className="text-xs text-gray-400">Loading the booklet…</p>

  if (!reviewable) {
    return (
      <EmptyState
        icon="fa-solid fa-hourglass-half"
        title="This booklet is not ready for evaluation"
        action={
          <SmallButton icon="fa-solid fa-arrow-left" onClick={onBack}>
            Back to the segments
          </SmallButton>
        }
      >
        {status === 'failed' && booklet.failure_reason
          ? `${FAILURE[booklet.failure_reason]} Delete the booklet and upload it again.`
          : 'The AI is still reading and scoring it. The marks appear here when it is done.'}
      </EmptyState>
    )
  }

  const progress = progressOf(answers)
  const attempted = answers.filter((a) => a.attempted)
  const previous = neighbour(answers, label, -1)
  const following = neighbour(answers, label, 1)
  const open = current ? nextOpen(answers, current.slot_label) : undefined
  const percent = progress.attempted
    ? Math.round((progress.approved / progress.attempted) * 100)
    : 0

  return (
    <div className="space-y-4">
      <GlassPanel className="flex flex-wrap items-center justify-between gap-3 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-gray-400">
            Evaluating Student:{' '}
            <strong className="text-white">
              {booklet.student.name} (USN: {booklet.student.usn})
            </strong>
          </span>
          {!summary && label && (
            <span className="text-gray-400">
              Question <strong className="text-white">{label}</strong>
            </span>
          )}
          <StatusBadge status={status} />
          {review.data?.amendment_in_progress && <Badge tone="amber">Draft amendment</Badge>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canEdit && (
            <span className="text-emerald-300">
              <i className="fa-solid fa-lock mr-1" aria-hidden="true" />
              You have this booklet open.
            </span>
          )}
          <SmallButton icon="fa-solid fa-arrow-left" tone="gray" onClick={onBack}>
            Back to Segments
          </SmallButton>
          <SmallButton
            icon="fa-solid fa-file-export"
            tone="purple"
            onClick={() => go({ summary: true })}
          >
            Save &amp; Export Grade Sheet
          </SmallButton>
        </div>
      </GlassPanel>

      {holder && (
        <p
          role="note"
          className="rounded-xl border border-amber-500/30 bg-amber-950/30 px-4 py-2 text-xs text-amber-100"
        >
          <i className="fa-solid fa-lock mr-1.5 text-amber-300" aria-hidden="true" />
          {readOnlyReason}
        </p>
      )}
      {lock.kind === 'error' && (
        <p
          role="alert"
          className="rounded-xl border border-red-500/30 bg-red-950/30 px-4 py-2 text-xs text-red-200"
        >
          {lock.message}
        </p>
      )}
      {(review.isError || segments.isError) && (
        <p role="alert" className="text-xs text-red-300">
          Part of this booklet could not be loaded. Reload the page to try again.
        </p>
      )}

      <GlassPanel as="nav" aria-label="Answers" className="space-y-3 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-48 flex-1">
            <div className="mb-1 flex justify-between text-[11px] text-gray-300">
              <span>
                {progress.approved} of {progress.attempted} answers approved
                {progress.skipped > 0 && `, ${progress.skipped} skipped`}
                {progress.drafts > 0 && `, ${progress.drafts} in amendment`}
              </span>
              <span>{percent}%</span>
            </div>
            <div
              role="progressbar"
              aria-label="Approval progress"
              aria-valuemin={0}
              aria-valuemax={progress.attempted}
              aria-valuenow={progress.approved}
              className="h-1.5 overflow-hidden rounded-full bg-gray-800"
            >
              <div
                className="h-full rounded-full bg-emerald-500"
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
          {summary ? (
            <SmallButton
              icon="fa-solid fa-arrow-left"
              tone="gray"
              disabled={!current}
              onClick={() => go({ summary: false })}
            >
              Back to the answers
            </SmallButton>
          ) : (
            <SmallButton
              icon="fa-solid fa-table-list"
              tone="gray"
              onClick={() => go({ summary: true })}
            >
              Booklet summary
            </SmallButton>
          )}
        </div>
        <ul className="flex flex-wrap items-center gap-1.5">
          {!summary && (
            <li>
              <SmallButton
                icon="fa-solid fa-chevron-left"
                tone="gray"
                disabled={!previous || previous.slot_label === label}
                onClick={() => previous && go({ answer: previous.slot_label })}
              >
                Previous answer
              </SmallButton>
            </li>
          )}
          {attempted.map((a) => {
            const stage = stageOf(a)
            const meta = CHIP[stage]
            const selected = !summary && a.slot_label === label
            return (
              <li key={a.id}>
                <button
                  type="button"
                  aria-label={`Question ${a.slot_label}, ${meta.label}`}
                  aria-current={selected ? 'step' : undefined}
                  onClick={() => go({ answer: a.slot_label, summary: false })}
                  className={`min-w-9 rounded-lg border px-2 py-1 text-xs font-semibold transition ${meta.dot} ${selected ? 'ring-2 ring-cyan-400' : 'hover:border-gray-400'}`}
                >
                  {a.slot_label}
                  {stage === 'approved' && (
                    <i className="fa-solid fa-check ml-1" aria-hidden="true" />
                  )}
                  {stage === 'skipped' && (
                    <i className="fa-solid fa-forward ml-1" aria-hidden="true" />
                  )}
                  {stage === 'draft' && <i className="fa-solid fa-pen ml-1" aria-hidden="true" />}
                </button>
              </li>
            )
          })}
          {!summary && (
            <>
              <li>
                <SmallButton
                  icon="fa-solid fa-chevron-right"
                  tone="gray"
                  disabled={!following || following.slot_label === label}
                  onClick={() => following && go({ answer: following.slot_label })}
                >
                  Next answer
                </SmallButton>
              </li>
              <li>
                <SmallButton
                  icon="fa-solid fa-forward-step"
                  tone="cyan"
                  disabled={!open}
                  onClick={() => open && go({ answer: open.slot_label })}
                >
                  Next to approve
                </SmallButton>
              </li>
            </>
          )}
        </ul>
      </GlassPanel>

      {!data && !review.isError && <p className="text-xs text-gray-400">Loading the marks…</p>}

      {data && summary && (
        <BookletSummary
          review={data}
          canEdit={canEdit}
          readOnlyReason={readOnlyReason}
          busy={busy}
          onOpenAnswer={(l) => go({ answer: l, summary: false })}
          onApproveBooklet={() => void approveBooklet()}
          onReopen={(a, reason) => void reopenAnswer(a, reason, true)}
          onWithdraw={(a) => void withdrawDraft(a)}
        />
      )}

      {data && !summary && current && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <AnswerMappingPanel
            label={current.slot_label}
            slot={slot}
            question={question.data}
            questionLoading={!!slot?.questionId && question.isPending}
            questionFailed={question.isError}
            views={ofAnswer}
            answer={current}
            overlays={overlays}
          />
          <DiagnosticPanel
            answer={current}
            question={question.data}
            ocrConfidence={ocrConfidence}
          />
          <GradingPanel
            key={`${current.id}:${current.status}:${current.draft?.amendment_id ?? ''}`}
            answer={current}
            bookletApproved={data.approved}
            readOnlyReason={readOnlyReason}
            busy={busy}
            onApprove={(d) => void approveAnswer(current, d)}
            onSkip={() => void skipAnswer(current)}
            onReopen={(reason) => void reopenAnswer(current, reason, false)}
            onWithdraw={() => void withdrawDraft(current)}
          />
        </div>
      )}

      {data && !summary && !current && (
        <EmptyState icon="fa-solid fa-inbox" title="No answers to evaluate">
          No text was assigned to any question of this exam. Go back to the segments and assign the
          text to the questions it answers.
        </EmptyState>
      )}
    </div>
  )
}
