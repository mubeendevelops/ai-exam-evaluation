import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import { useDebounced } from '../../hooks/useDebounced'
import { formatMark } from '../../lib/review'
import { StatusBadge } from '../evaluate/StatusBadge'
import { SelectInput, SmallButton, TextInput } from '../schema/controls'
import { EmptyState, GlassPanel, Modal, useToast } from '../ui'
import { DownloadSheetButton, sheetFileName } from './DownloadSheetButton'

export type Evaluated = components['schemas']['EvaluatedBookletOut']
type Status = Evaluated['status']

const PAGE = 20

function when(iso: string): string {
  return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

/**
 * The evaluated booklets database: every approved booklet of the college with its result sheet
 * versions. Search by exam, student, USN and status; download any version; delete the booklet
 * (after a confirmation that says what goes).
 */
export function EvaluatedList() {
  const [exam, setExam] = useState('')
  const [student, setStudent] = useState('')
  const [usn, setUsn] = useState('')
  const [status, setStatus] = useState<'' | Status>('')
  const [offset, setOffset] = useState(0)
  const [doomed, setDoomed] = useState<Evaluated | null>(null)
  const filters = useDebounced({ exam, student, usn, status }, 300)

  const list = useQuery({
    queryKey: ['evaluated', filters, offset],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/evaluated-booklets', {
        params: {
          query: {
            exam: filters.exam.trim(),
            student: filters.student.trim(),
            usn: filters.usn.trim(),
            status: filters.status || undefined,
            limit: PAGE,
            offset,
          },
        },
      })
      if (!data) throw new Error('evaluated booklets unavailable')
      return data
    },
    placeholderData: (previous) => previous,
  })

  const change = (set: (value: string) => void) => (value: string) => {
    set(value)
    setOffset(0)
  }
  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const filtered = !!(filters.exam || filters.student || filters.usn || filters.status)

  return (
    <div className="space-y-4">
      <GlassPanel as="section" aria-label="Search evaluated booklets" className="p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <TextInput
            label="Exam"
            placeholder="Title or course code"
            value={exam}
            onChange={(e) => change(setExam)(e.target.value)}
          />
          <TextInput
            label="Student"
            placeholder="Part of the name"
            value={student}
            onChange={(e) => change(setStudent)(e.target.value)}
          />
          <TextInput
            label="USN"
            placeholder="Part of the USN"
            value={usn}
            onChange={(e) => change(setUsn)(e.target.value)}
          />
          <SelectInput
            label="Status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as '' | Status)
              setOffset(0)
            }}
          >
            <option value="">Any status</option>
            <option value="approved">Approved</option>
            <option value="approved_amended">Approved (amended)</option>
            <option value="amendment_in_progress">Amendment in progress</option>
          </SelectInput>
        </div>
      </GlassPanel>

      {list.isError ? (
        <p role="alert" className="text-sm text-red-300">
          The evaluated booklets could not be loaded.{' '}
          <button type="button" className="underline" onClick={() => void list.refetch()}>
            Try again
          </button>
        </p>
      ) : list.isPending ? (
        <p role="status" className="text-sm text-gray-400">
          Loading evaluated booklets…
        </p>
      ) : items.length === 0 ? (
        <EmptyState icon="fa-solid fa-box-archive" title="No evaluated booklets">
          {filtered
            ? 'No approved booklet matches this search.'
            : 'Booklets appear here once their marks are approved and a result sheet is issued.'}
        </EmptyState>
      ) : (
        <GlassPanel as="section" aria-label="Evaluated booklets" className="p-4">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">
                Approved booklets with their result sheet versions
              </caption>
              <thead>
                <tr className="border-b border-gray-800 text-[10px] tracking-wider text-gray-400 uppercase">
                  <th scope="col" className="py-2 pr-3">
                    Student
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Exam
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Status
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Result
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Evaluated
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Result sheets
                  </th>
                  <th scope="col" className="py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((b) => (
                  <Row key={b.id} booklet={b} onDelete={() => setDoomed(b)} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-gray-400">
            <span>
              {offset + 1}–{offset + items.length} of {total}
            </span>
            <span className="flex gap-2">
              <SmallButton
                tone="gray"
                icon="fa-solid fa-chevron-left"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                Previous
              </SmallButton>
              <SmallButton
                tone="gray"
                icon="fa-solid fa-chevron-right"
                disabled={offset + PAGE >= total}
                onClick={() => setOffset(offset + PAGE)}
              >
                Next
              </SmallButton>
            </span>
          </div>
        </GlassPanel>
      )}

      {doomed && <DeleteDialog booklet={doomed} onClose={() => setDoomed(null)} />}
    </div>
  )
}

function Row({ booklet: b, onDelete }: { booklet: Evaluated; onDelete: () => void }) {
  const navigate = useNavigate()
  return (
    <tr className="border-b border-gray-800/70 align-top text-gray-200">
      <td className="py-2 pr-3">
        <span className="font-semibold text-white">{b.student.name}</span>
        <br />
        <span className="font-mono text-[11px] text-gray-400">{b.student.usn}</span>
      </td>
      <td className="py-2 pr-3">
        {b.exam}
        {b.course_code && <span className="block text-[11px] text-gray-400">{b.course_code}</span>}
      </td>
      <td className="py-2 pr-3">
        <StatusBadge status={b.status} />
      </td>
      <td className="py-2 pr-3 font-mono">
        {formatMark(b.total)} / {formatMark(b.max_marks)}
      </td>
      <td className="py-2 pr-3 text-gray-300">{when(b.evaluated_at)}</td>
      <td className="py-2 pr-3">
        <span className="flex flex-wrap gap-1.5">
          {b.sheets.map((s) => (
            <DownloadSheetButton
              key={s.version}
              url={s.pdf_url}
              filename={sheetFileName(b.student.usn, s.version)}
              label={`v${s.version} PDF`}
              tone={s.version === b.sheet_version ? 'purple' : 'gray'}
            />
          ))}
        </span>
      </td>
      <td className="py-2 text-right">
        <span className="inline-flex flex-wrap justify-end gap-1.5">
          <SmallButton
            tone="gray"
            icon="fa-solid fa-eye"
            onClick={() => void navigate(`/evaluate?booklet=${b.id}&step=3&view=summary`)}
          >
            Open <span className="sr-only">the booklet of {b.student.name}</span>
          </SmallButton>
          <SmallButton tone="gray" icon="fa-solid fa-trash" onClick={onDelete}>
            Delete <span className="sr-only">the booklet of {b.student.name}</span>
          </SmallButton>
        </span>
      </td>
    </tr>
  )
}

function DeleteDialog({ booklet: b, onClose }: { booklet: Evaluated; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const remove = useMutation({
    mutationFn: async () => {
      const { response, error: problem } = await api.DELETE('/api/v1/booklets/{booklet_id}', {
        params: { path: { booklet_id: b.id } },
      })
      if (!response.ok) throw new Error(problemOf(problem, 'Could not delete the booklet.').message)
    },
    onSuccess: async () => {
      toast.show('The booklet was deleted. A record without content stays in the audit log.')
      onClose()
      await queryClient.invalidateQueries({ queryKey: ['evaluated'] })
      await queryClient.invalidateQueries({ queryKey: ['booklets'] })
    },
    onError: (e) => setError(e instanceof Error ? e.message : NETWORK_PROBLEM),
  })
  const versions =
    b.sheets.length === 1 ? 'its result sheet' : `all ${b.sheets.length} result sheets`
  return (
    <Modal
      open
      onClose={onClose}
      title="Delete this evaluated booklet?"
      description={`${b.student.name} (${b.student.usn}), ${b.exam}`}
      footer={
        <div className="flex justify-end gap-2">
          <SmallButton tone="gray" onClick={onClose}>
            Cancel
          </SmallButton>
          <SmallButton
            tone="purple"
            icon="fa-solid fa-trash"
            disabled={remove.isPending}
            onClick={() => {
              setError(null)
              remove.mutate()
            }}
          >
            Delete booklet
          </SmallButton>
        </div>
      }
    >
      <div className="space-y-2 text-sm text-gray-200">
        <p>
          The page images, the text, every mark and {versions} are deleted for good. Download the
          PDFs first if you still need them.
        </p>
        <p className="text-xs text-gray-400">
          The audit log keeps a record that the booklet was deleted (who and when), and nothing of
          its content.
        </p>
        {error && (
          <p role="alert" className="text-xs text-red-300">
            {error}
          </p>
        )}
      </div>
    </Modal>
  )
}
