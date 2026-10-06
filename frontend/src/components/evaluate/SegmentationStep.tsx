import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { buildSegments, indexRegions, type SegmentView } from '../../lib/segments'
import { SmallButton } from '../schema/controls'
import { EmptyState, GlassPanel, useToast } from '../ui'
import { DiagramPanel } from './DiagramPanel'
import { FAILURE, STATUS, isApproved, isReviewable, type BookletDetail } from './model'
import { PagePanel } from './PagePanel'
import {
  useBookletDetail,
  useBookletLock,
  reviewKey,
  useExamLabels,
  usePageTexts,
  useReview,
  useSegments,
  useStudentDiagrams,
  type Review,
} from './queries'
import { RescorePanel } from './RescorePanel'
import { SegmentCard, type SegmentActions } from './SegmentCard'
import { StatusBadge } from './StatusBadge'
import { useGuardedEdit, useRescoreLog } from './useEdits'

const WAIT_FOR_WORKER_MS = 90_000

function progressLine(b: BookletDetail, segmenting: boolean): string | null {
  if (segmenting) return 'Segmenting again with your corrected text…'
  switch (b.status) {
    case 'uploaded':
      return 'Waiting in the queue. Booklets are processed one at a time.'
    case 'processing':
      return `Cleaning pages: ${b.pages_cleaned} of ${b.page_count || '…'}.`
    case 'pages_ready':
      return 'Pages are clean. Reading the handwriting starts next.'
    case 'reading':
      return `Reading handwriting: ${b.pages_read} of ${b.page_count} pages.`
    case 'text_ready':
      return 'Handwriting read. Splitting it into answers…'
    case 'segmented':
      return 'Answers found. Suggesting marks…'
    default:
      return null
  }
}

function leaveNote(status: BookletDetail['status']): string {
  return isApproved(status)
    ? 'This booklet is approved. Segments cannot be changed here: amend single answers from the evaluation view.'
    : ''
}

/**
 * Step 2, "AI Segmentation": the page with a box per answer, the segments with their OCR text,
 * and the teacher's tools. Opening the booklet takes its lock (P15), every edit is one API call
 * that names the booklet version it was based on, and the re-scoring each edit causes is shown
 * as the new suggestions arrive.
 */
export function SegmentationStep({
  bookletId,
  onBack,
  onProceed,
}: {
  bookletId: string
  onBack: () => void
  onProceed: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const detail = useBookletDetail(bookletId)
  const booklet = detail.data
  const status = booklet?.status
  const reviewable = status !== undefined && isReviewable(status)
  const hasSegments = reviewable || status === 'segmented'

  const [resegmenting, setResegmenting] = useState<{ version: number; at: number } | null>(null)
  const [confirmResegment, setConfirmResegment] = useState(false)
  const { lock, reopen } = useBookletLock(bookletId, reviewable)
  const review = useReview(bookletId, reviewable, resegmenting !== null)
  const segments = useSegments(bookletId, hasSegments)
  const { texts } = usePageTexts(booklet)
  const diagrams = useStudentDiagrams(bookletId, hasSegments)
  const labelsQuery = useExamLabels(booklet?.blueprint.id)
  const guarded = useGuardedEdit(bookletId, reopen)
  const rescore = useRescoreLog(review.data)

  const [selected, setSelected] = useState<string | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const views = useMemo(
    () => buildSegments(segments.data?.segments ?? [], texts),
    [segments.data, texts],
  )
  const labels = useMemo(() => {
    const all = new Set(labelsQuery.data ?? [])
    for (const v of views) if (v.label) all.add(v.label)
    return [...all]
  }, [labelsQuery.data, views])
  const lines = useMemo(() => indexRegions(texts), [texts])
  const answers = new Map((review.data?.answers ?? []).map((a) => [a.slot_label, a]))

  const approved = status !== undefined && isApproved(status)
  const canEdit = lock.kind === 'mine' && review.data?.status === 'in_review' && !resegmenting
  const waiting = review.data?.answers.some((a) => a.rescore_pending) ?? false
  const working = status !== undefined && STATUS[status].working
  const scanning = working || resegmenting !== null || waiting

  // Re-segmenting is finished when the worker has moved the booklet's version on. The check
  // runs when the cache changes (a subscription), and a timer gives up waiting.
  const { refresh } = guarded
  useEffect(() => {
    if (!resegmenting) return
    let finished = false
    const finish = (text: string, tone?: 'amber') => {
      if (finished) return
      finished = true
      setResegmenting(null)
      toast.show(text, tone)
    }
    const check = () => {
      if (finished) return
      const now = queryClient.getQueryData<Review>(reviewKey(bookletId))?.version
      if (now !== undefined && now !== resegmenting.version) {
        finish('The booklet was segmented again. Check the new split.')
        void refresh()
      }
    }
    const unsubscribe = queryClient.getQueryCache().subscribe(check)
    const timer = setTimeout(
      () =>
        finish(
          'The worker has not answered yet. If nothing changes, the booklet was edited meanwhile: try again.',
          'amber',
        ),
      Math.max(resegmenting.at + WAIT_FOR_WORKER_MS - Date.now(), 0),
    )
    return () => {
      unsubscribe()
      clearTimeout(timer)
    }
  }, [resegmenting, bookletId, queryClient, toast, refresh])

  // Picking a segment (from the page or the list) shows its page and brings its card into view.
  const select = (segmentId: string) => {
    setSelected(segmentId)
    const view = views.find((v) => v.segment.id === segmentId)
    if (view && view.pages.length > 0 && !view.pages.includes(pageNumber)) {
      setPageNumber(view.pages[0] ?? 1)
    }
    document.getElementById(`segment-${segmentId}`)?.scrollIntoView?.({ block: 'nearest' })
  }

  const labelOf = (answerIds: string[]) =>
    (review.data?.answers ?? []).filter((a) => answerIds.includes(a.id)).map((a) => a.slot_label)
  const rescoringNote = (ids: string[] = []) => {
    const names = labelOf(ids)
    return names.length > 0 ? ` Re-scoring question ${names.join(', ')}.` : ''
  }

  const act = async <T,>(work: () => Promise<T>): Promise<T> => {
    setBusy(true)
    try {
      return await work()
    } finally {
      setBusy(false)
    }
  }

  const actions: SegmentActions = {
    saveLine: (lineId, change) =>
      act(async () => {
        const done = await guarded.run(
          (v) =>
            api.POST('/api/v1/booklets/{booklet_id}/regions/{region_id}', {
              params: { path: { booklet_id: bookletId, region_id: lineId } },
              body: { expected_version: v, text: change.text, struck_out: change.struck },
            }),
          'Could not save the line.',
        )
        if (done) {
          toast.show(
            change.text !== undefined
              ? `Line saved and kept for the OCR benchmark.${rescoringNote(done.rescoring)}`
              : `Line ${change.struck ? 'left out of' : 'put back into'} scoring.${rescoringNote(done.rescoring)}`,
          )
        }
        return done !== undefined
      }),
    merge: (first, second) =>
      act(async () => {
        const done = await guarded.run(
          (v) =>
            api.POST('/api/v1/booklets/{booklet_id}/segments/merge', {
              params: { path: { booklet_id: bookletId } },
              body: { expected_version: v, first, second },
            }),
          'Could not merge the segments.',
        )
        if (done) toast.show(`Segments merged.${rescoringNote(done.rescoring)}`)
        setEditing(null)
      }),
    split: (segmentId, regionId) =>
      act(async () => {
        const done = await guarded.run(
          (v) =>
            api.POST('/api/v1/booklets/{booklet_id}/segments/split', {
              params: { path: { booklet_id: bookletId } },
              body: {
                expected_version: v,
                segment_id: segmentId,
                at_region: regionId,
                label: null,
              },
            }),
          'Could not split the segment.',
        )
        if (done)
          toast.show(
            `Split. The new segment is in the unassigned tray.${rescoringNote(done.rescoring)}`,
          )
        setEditing(null)
      }),
    reassign: (segmentId, label) =>
      act(async () => {
        const done = await guarded.run(
          (v) =>
            api.POST('/api/v1/booklets/{booklet_id}/segments/reassign', {
              params: { path: { booklet_id: bookletId } },
              body: { expected_version: v, segment_id: segmentId, label },
            }),
          'Could not reassign the segment.',
        )
        if (done) {
          toast.show(
            `${label === null ? 'Moved to the unassigned tray.' : `Assigned to question ${label}.`}${rescoringNote(done.rescoring)}`,
          )
        }
      }),
    moveBoundary: (upper, lower, regionId) =>
      act(async () => {
        const done = await guarded.run(
          (v) =>
            api.POST('/api/v1/booklets/{booklet_id}/segments/move-boundary', {
              params: { path: { booklet_id: bookletId } },
              body: { expected_version: v, upper, lower, region: regionId },
            }),
          'Could not move the boundary.',
        )
        if (done) toast.show(`Boundary moved.${rescoringNote(done.rescoring)}`)
        setEditing(null)
      }),
  }

  async function resegment() {
    setConfirmResegment(false)
    await act(async () => {
      const done = await guarded.run(
        (v) =>
          api.POST('/api/v1/booklets/{booklet_id}/segments/resegment', {
            params: { path: { booklet_id: bookletId } },
            body: { expected_version: v },
          }),
        'Could not queue the new segmentation.',
      )
      if (done) setResegmenting({ version: done.booklet_version, at: Date.now() })
    })
  }

  if (detail.isError) {
    return (
      <EmptyState
        icon="fa-solid fa-file-circle-question"
        title="This booklet could not be opened"
        action={
          <SmallButton icon="fa-solid fa-arrow-left" onClick={onBack}>
            Back to the submissions
          </SmallButton>
        }
      >
        It may have been deleted, or it belongs to another college.
      </EmptyState>
    )
  }
  if (!booklet || !status) return <p className="text-xs text-gray-400">Loading the booklet…</p>

  const editingLine = editing ? (lines.get(editing) ?? null) : null
  const assigned = views.filter((v) => v.label !== null)
  const tray = views.filter((v) => v.label === null)
  const neighbours = (v: SegmentView) => {
    const at = views.indexOf(v)
    return { prev: views[at - 1] ?? null, next: views[at + 1] ?? null }
  }
  const card = (v: SegmentView) => {
    const { prev, next } = neighbours(v)
    return (
      <div key={v.segment.id} id={`segment-${v.segment.id}`}>
        <SegmentCard
          view={v}
          prev={prev}
          next={next}
          answer={v.label ? answers.get(v.label) : undefined}
          labels={labels}
          selected={selected === v.segment.id}
          onSelect={() => select(v.segment.id)}
          canEdit={canEdit}
          busy={busy}
          editingLine={editing}
          onEditLine={setEditing}
          actions={actions}
        />
      </div>
    )
  }

  const holder = lock.kind === 'other' ? lock : null
  const readOnlyReason = holder
    ? `${holder.holder ?? 'Another teacher'} has this booklet open${holder.until ? ` until ${new Date(holder.until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}. You can look, but not change anything.`
    : approved
      ? leaveNote(status)
      : lock.kind === 'error'
        ? lock.message
        : undefined

  return (
    <div className="space-y-6">
      <GlassPanel className="flex flex-wrap items-center justify-between gap-3 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          <SmallButton icon="fa-solid fa-arrow-left" tone="gray" onClick={onBack}>
            Submissions
          </SmallButton>
          <span className="text-gray-400">
            Booklet of{' '}
            <strong className="text-white">
              {booklet.student.name} ({booklet.student.usn})
            </strong>{' '}
            · {booklet.blueprint.title}
          </span>
          <StatusBadge status={status} />
        </div>
        {canEdit && (
          <span className="text-emerald-300">
            <i className="fa-solid fa-lock mr-1" aria-hidden="true" />
            You have this booklet open. Every change is saved at once.
          </span>
        )}
      </GlassPanel>

      {readOnlyReason && (
        <p
          role="note"
          className="rounded-xl border border-amber-500/30 bg-amber-950/30 px-4 py-2 text-xs text-amber-100"
        >
          <i className="fa-solid fa-eye mr-1.5 text-amber-300" aria-hidden="true" />
          {readOnlyReason}
        </p>
      )}
      {status === 'needs_retake' && (
        <p
          role="alert"
          className="rounded-xl border border-amber-500/30 bg-amber-950/30 px-4 py-2 text-xs text-amber-100"
        >
          Some pages need a retake before the booklet can be read. Go back to the submissions to
          retake them or to go on with them as they are.
        </p>
      )}
      {status === 'failed' && (
        <p
          role="alert"
          className="rounded-xl border border-red-500/30 bg-red-950/30 px-4 py-2 text-xs text-red-200"
        >
          {booklet.failure_reason ? FAILURE[booklet.failure_reason] : 'Processing failed.'} Delete
          the booklet and upload it again.
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <PagePanel
          booklet={booklet}
          views={views}
          selected={selected}
          onSelect={select}
          editingLine={editingLine}
          scanning={scanning}
          status={progressLine(booklet, resegmenting !== null)}
          pageNumber={pageNumber}
          onPage={setPageNumber}
        />

        <GlassPanel className="flex flex-col justify-between p-5 lg:col-span-6">
          <div>
            <div className="mb-4 flex items-center justify-between border-b border-gray-800 pb-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
                <i className="fa-solid fa-scissors text-purple-400" aria-hidden="true" />
                AI Cropped &amp; OCR Extracted Segments
              </h3>
              <span className="text-[11px] text-gray-400">
                {assigned.length} {assigned.length === 1 ? 'answer' : 'answers'}
                {tray.length > 0 && `, ${tray.length} unassigned`}
              </span>
            </div>

            {!hasSegments && (
              <p className="text-xs text-gray-400">
                The segments appear here once the handwriting has been read and split into answers.
              </p>
            )}
            {segments.isError && (
              <p role="alert" className="text-xs text-red-300">
                The segments could not be loaded.
              </p>
            )}
            <div className="space-y-4">{assigned.map(card)}</div>

            {hasSegments && (
              <section aria-label="Unassigned tray" className="mt-6">
                <h4 className="mb-2 flex items-center gap-2 text-xs font-bold tracking-wider text-amber-400 uppercase">
                  <i className="fa-solid fa-inbox" aria-hidden="true" />
                  Unassigned tray ({tray.length})
                </h4>
                {tray.length === 0 ? (
                  <p className="text-xs text-gray-400">
                    Every piece of text is assigned to a question.
                  </p>
                ) : (
                  <>
                    <p className="mb-2 text-[11px] text-gray-400">
                      Text the AI could not place. Assign each piece to a question, or merge it into
                      the segment next to it.
                    </p>
                    <div className="space-y-4">{tray.map(card)}</div>
                  </>
                )}
              </section>
            )}
          </div>

          <div className="mt-6 space-y-3 border-t border-gray-800 pt-4">
            {confirmResegment && (
              <div
                role="alertdialog"
                aria-label="Segment again"
                className="space-y-2 rounded-xl border border-amber-500/30 bg-amber-950/30 p-3 text-xs text-amber-100"
              >
                <p>
                  Segment this booklet again? The AI splits the text afresh and its result replaces
                  every segment, including the ones you changed. Your corrections to the text stay.
                  Answers whose text changes are scored again.
                </p>
                <div className="flex gap-2">
                  <SmallButton
                    tone="purple"
                    icon="fa-solid fa-check"
                    onClick={() => void resegment()}
                  >
                    Yes, segment again
                  </SmallButton>
                  <SmallButton tone="gray" onClick={() => setConfirmResegment(false)}>
                    Cancel
                  </SmallButton>
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center justify-end gap-3">
              <SmallButton
                icon="fa-solid fa-wand-magic-sparkles"
                tone="gray"
                disabled={!canEdit || busy}
                onClick={() => setConfirmResegment(true)}
              >
                Re-Segment
              </SmallButton>
              <button
                type="button"
                onClick={onProceed}
                disabled={!reviewable || waiting || resegmenting !== null}
                className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg transition disabled:opacity-50"
              >
                <span>Proceed to Evaluation</span>
                <i className="fa-solid fa-arrow-right" aria-hidden="true" />
              </button>
            </div>
          </div>
        </GlassPanel>
      </div>

      <RescorePanel entries={rescore.entries} onDismiss={rescore.dismiss} />

      <DiagramPanel
        booklet={booklet}
        diagrams={diagrams.data ?? []}
        views={views}
        texts={texts}
        readOnlyReason={
          canEdit ? undefined : (readOnlyReason ?? 'The booklet is not open for changes yet.')
        }
        guarded={guarded}
      />
    </div>
  )
}
