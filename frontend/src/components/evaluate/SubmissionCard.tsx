import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { SmallButton } from '../schema/controls'
import { GlassPanel, useToast } from '../ui'
import { FAILURE, STATUS, isReviewable, resultText, type Booklet } from './model'
import { StatusBadge } from './StatusBadge'

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
          <span className="text-[11px] text-amber-300">
            <i className="fa-solid fa-camera-rotate mr-1" aria-hidden="true" />
            Waiting for a retake by your admin
          </span>
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
    </GlassPanel>
  )
}
