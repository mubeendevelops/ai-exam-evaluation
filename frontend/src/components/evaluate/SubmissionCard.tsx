import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { AuthImage } from '../qna/AuthImage'
import { SmallButton } from '../schema/controls'
import { GlassPanel, useToast } from '../ui'
import { FAILURE, RETAKE_REASON, STATUS, isReviewable, resultText, type Booklet } from './model'
import { StatusBadge } from './StatusBadge'

/** The pages the quality gate flagged, with why, and the teacher's choice: use anyway, or retake. */
function RetakePanel({ booklet }: { booklet: Booklet }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const detail = useQuery({
    queryKey: ['booklet', booklet.id],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets/{booklet_id}', {
        params: { path: { booklet_id: booklet.id } },
      })
      if (!data) throw new Error('booklet unavailable')
      return data
    },
  })

  const proceed = useMutation({
    mutationFn: async (number: number) => {
      const { data, error } = await api.POST(
        '/api/v1/booklets/{booklet_id}/pages/{number}/use-anyway',
        {
          params: { path: { booklet_id: booklet.id, number } },
        },
      )
      if (!data) throw new Error(problemOf(error, 'Could not go on with that page.').message)
    },
    onSuccess: async (_none, number) => {
      toast.show(`Going on with page ${number}.`)
      await queryClient.invalidateQueries({ queryKey: ['booklets'] })
      await queryClient.invalidateQueries({ queryKey: ['booklet', booklet.id] })
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : NETWORK_PROBLEM, 'red'),
  })

  const remove = useMutation({
    mutationFn: async () => {
      const { response, error } = await api.DELETE('/api/v1/booklets/{booklet_id}', {
        params: { path: { booklet_id: booklet.id } },
      })
      if (!response.ok) throw new Error(problemOf(error, 'Could not delete the booklet.').message)
    },
    onSuccess: async () => {
      toast.show('Deleted. Photograph the flagged pages again and upload the booklet.')
      await queryClient.invalidateQueries({ queryKey: ['booklets'] })
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : NETWORK_PROBLEM, 'red'),
  })

  const flagged = (detail.data?.pages ?? []).filter((p) => booklet.flagged_pages.includes(p.number))
  return (
    <div
      role="region"
      aria-label={`Retake needed for ${booklet.student.name}`}
      className="mt-3 space-y-3 rounded-xl border border-amber-500/30 bg-amber-950/20 p-3 text-xs"
    >
      <p className="text-amber-100">
        <i className="fa-solid fa-camera-rotate mr-1.5 text-amber-300" aria-hidden="true" />
        {flagged.length === 1 ? '1 page needs' : `${booklet.flagged_pages.length} pages need`} a
        retake. Retake and upload the booklet again, or go on with the page as it is.
      </p>
      {detail.isPending && <p className="text-gray-400">Loading the pages…</p>}
      {detail.isError && <p className="text-red-400">The pages could not be loaded.</p>}
      <ul className="space-y-2">
        {flagged.map((p) => (
          <li
            key={p.number}
            className="flex flex-wrap items-start gap-3 rounded-lg border border-gray-800 bg-gray-900/70 p-2"
          >
            {p.image_url && <AuthImage path={p.image_url} alt={`Page ${p.number}`} />}
            <div className="min-w-0 grow space-y-1">
              <strong className="text-white">Page {p.number}</strong>
              <ul className="list-disc pl-4 text-gray-200">
                {p.retake_reasons.map((r) => (
                  <li key={r}>{RETAKE_REASON[r]}</li>
                ))}
              </ul>
              <SmallButton
                icon="fa-solid fa-forward"
                tone="purple"
                disabled={proceed.isPending}
                onClick={() => proceed.mutate(p.number)}
              >
                {`Use page ${p.number} anyway`}
              </SmallButton>
            </div>
          </li>
        ))}
      </ul>
      {confirmDelete ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-gray-200">Delete this booklet and its pages?</span>
          <SmallButton tone="gray" disabled={remove.isPending} onClick={() => remove.mutate()}>
            Yes, delete it
          </SmallButton>
          <SmallButton tone="gray" onClick={() => setConfirmDelete(false)}>
            Keep it
          </SmallButton>
        </div>
      ) : (
        <SmallButton icon="fa-solid fa-trash" tone="gray" onClick={() => setConfirmDelete(true)}>
          Delete and upload again
        </SmallButton>
      )}
    </div>
  )
}

/** One uploaded booklet: who, how many pages, where it is, its result, and what to do next. */
export function SubmissionCard({
  booklet,
  onOpen,
}: {
  booklet: Booklet
  onOpen: (id: string) => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [retake, setRetake] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const remove = useMutation({
    mutationFn: async () => {
      const { response, error } = await api.DELETE('/api/v1/booklets/{booklet_id}', {
        params: { path: { booklet_id: booklet.id } },
      })
      if (!response.ok) throw new Error(problemOf(error, 'Could not delete the booklet.').message)
    },
    onSuccess: async () => {
      toast.show('The booklet was deleted. A record without content stays in the audit log.')
      await queryClient.invalidateQueries({ queryKey: ['booklets'] })
    },
    onError: (e) => {
      setConfirming(false)
      toast.show(e instanceof Error ? e.message : NETWORK_PROBLEM, 'red')
    },
  })

  const meta = STATUS[booklet.status]
  const needsRetake = booklet.status === 'needs_retake'
  const reviewable = isReviewable(booklet.status)
  return (
    <GlassPanel
      as="article"
      aria-label={`Submission ${booklet.student.name}`}
      className="flex flex-col gap-3 p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-bold text-white">{booklet.student.name}</h4>
          <p className="font-mono text-[11px] text-gray-400">USN: {booklet.student.usn}</p>
          <p className="truncate text-[11px] text-gray-500">{booklet.blueprint.title}</p>
        </div>
        <StatusBadge status={booklet.status} />
      </div>

      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-gray-400">
        <div className="flex gap-1">
          <dt>Pages</dt>
          <dd className="text-gray-200">
            {booklet.page_count === 0 ? '—' : booklet.page_count}
            {meta.working &&
              booklet.page_count > 0 &&
              ` (${booklet.pages_cleaned} cleaned, ${booklet.pages_read} read)`}
          </dd>
        </div>
        <div className="flex gap-1">
          <dt>Result</dt>
          <dd className="text-gray-200">{resultText(booklet)}</dd>
        </div>
      </dl>
      {booklet.status === 'failed' && booklet.failure_reason && (
        <p role="alert" className="text-xs text-red-300">
          {FAILURE[booklet.failure_reason]}
        </p>
      )}
      {booklet.needs_text_pages.length > 0 && (
        <p className="text-[11px] text-amber-300">
          No engine could read page {booklet.needs_text_pages.join(', ')}.
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        {needsRetake ? (
          <SmallButton
            icon="fa-solid fa-camera-rotate"
            tone="purple"
            onClick={() => setRetake((v) => !v)}
          >
            {retake ? 'Hide pages' : 'Review flagged pages'}
          </SmallButton>
        ) : booklet.status === 'failed' ? (
          <span />
        ) : (
          <button
            type="button"
            onClick={() => onOpen(booklet.id)}
            className="rounded-lg bg-cyan-600 px-3 py-1 text-[11px] font-semibold text-white hover:bg-cyan-500"
          >
            {reviewable ? 'Open' : 'Run AI Eval'}
          </button>
        )}
        {confirming ? (
          <span className="flex items-center gap-2 text-[11px] text-gray-200">
            Delete?
            <SmallButton tone="gray" disabled={remove.isPending} onClick={() => remove.mutate()}>
              Yes
            </SmallButton>
            <SmallButton tone="gray" onClick={() => setConfirming(false)}>
              No
            </SmallButton>
          </span>
        ) : (
          <button
            type="button"
            aria-label={`Delete the booklet of ${booklet.student.name}`}
            title="Delete this booklet"
            onClick={() => setConfirming(true)}
            className="rounded-md p-1.5 text-xs text-gray-500 hover:text-red-400"
          >
            <i className="fa-solid fa-trash" aria-hidden="true" />
          </button>
        )}
      </div>
      {needsRetake && retake && <RetakePanel booklet={booklet} />}
    </GlassPanel>
  )
}
