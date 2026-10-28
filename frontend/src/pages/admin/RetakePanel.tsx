import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api } from '../../api/client'
import { replaceBooklet } from '../../api/booklets'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { FAILURE, RETAKE_REASON, type Booklet } from '../../components/evaluate/model'
import { AuthImage } from '../../components/qna/AuthImage'
import { useToast } from '../../components/ui'
import { RowButton } from './controls'

/**
 * The admin's retake decisions for one booklet (P26): the pages the quality gate flagged, with
 * why and their cleaned image (shown to the admin only while the retake is pending), "use
 * anyway" per page, and "Replace file" with a new PDF. A failed booklet can only be replaced.
 */
export function RetakePanel({ booklet, unitId }: { booklet: Booklet; unitId: string }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const flaggedBooklet = booklet.status === 'needs_retake'
  const detail = useQuery({
    queryKey: ['booklet', booklet.id],
    enabled: flaggedBooklet,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/booklets/{booklet_id}', {
        params: { path: { booklet_id: booklet.id } },
      })
      if (!data) throw new Error('booklet unavailable')
      return data
    },
  })
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['unit', unitId] })
    await queryClient.invalidateQueries({ queryKey: ['units'] })
  }

  const proceed = useMutation({
    mutationFn: async (number: number) => {
      const { data, error } = await api.POST(
        '/api/v1/booklets/{booklet_id}/pages/{number}/use-anyway',
        { params: { path: { booklet_id: booklet.id, number } } },
      )
      if (!data) throw new Error(problemOf(error, 'Could not go on with that page.').message)
    },
    onSuccess: async (_none, number) => {
      toast.show(`Going on with page ${number} of ${booklet.student.usn}.`)
      await queryClient.invalidateQueries({ queryKey: ['booklet', booklet.id] })
      await refresh()
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : NETWORK_PROBLEM, 'red'),
  })

  async function replace(file: File | undefined) {
    if (!file) return
    setProblem(null)
    if (!/\.pdf$/i.test(file.name) && file.type !== 'application/pdf') {
      setProblem('Choose a PDF.')
      return
    }
    const result = await replaceBooklet(booklet.id, file)
    if (input.current) input.current.value = ''
    if (result.ok) {
      toast.show(`${booklet.student.usn}: the new PDF is queued.`)
      await refresh()
    } else setProblem(result.message)
  }

  const flagged = (detail.data?.pages ?? []).filter((p) => booklet.flagged_pages.includes(p.number))
  return (
    <div
      role="region"
      aria-label={`Retake for ${booklet.student.usn}`}
      className="space-y-3 rounded-xl border border-amber-500/30 bg-amber-950/20 p-3 text-xs"
    >
      {booklet.status === 'failed' && booklet.failure_reason && (
        <p className="text-red-300">{FAILURE[booklet.failure_reason]}</p>
      )}
      {flaggedBooklet && detail.isPending && <p className="text-gray-400">Loading the pages…</p>}
      {flaggedBooklet && detail.isError && (
        <p className="text-red-400">The pages could not be loaded.</p>
      )}
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
              <RowButton disabled={proceed.isPending} onClick={() => proceed.mutate(p.number)}>
                {`Use page ${p.number} anyway`}
              </RowButton>
            </div>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={input}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          aria-label={`New PDF for ${booklet.student.usn}`}
          onChange={(e) => void replace(e.target.files?.[0])}
        />
        <RowButton onClick={() => input.current?.click()}>
          <i className="fa-solid fa-file-arrow-up mr-1" aria-hidden="true" />
          Replace file
        </RowButton>
        <span className="text-gray-400">
          The booklet is replaced by the new PDF; nothing else in the unit changes.
        </span>
      </div>
      {problem && (
        <p role="alert" className="text-red-300">
          {problem}
        </p>
      )}
    </div>
  )
}
