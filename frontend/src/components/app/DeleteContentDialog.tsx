import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { CancelButton } from '../../pages/admin/controls'
import { Modal } from '../ui'

type Kind = 'question' | 'paper'

const WORDS: Record<Kind, { title: string; gone: string }> = {
  question: {
    title: 'Delete this question?',
    gone: 'every version of the question, its reference answers, rubric, glossary, key files and reference diagrams',
  },
  paper: { title: 'Delete this paper?', gone: 'every version of the paper' },
}

async function check(kind: Kind, id: string) {
  const { data } =
    kind === 'question'
      ? await api.GET('/api/v1/questions/{question_id}/deletion', {
          params: { path: { question_id: id } },
        })
      : await api.GET('/api/v1/blueprints/{blueprint_id}/deletion', {
          params: { path: { blueprint_id: id } },
        })
  if (!data) throw new Error('deletion check unavailable')
  return data
}

/**
 * Permanent deletion of a question or paper (P28, D166). The dialog asks the server first and,
 * while a unit that is not frozen still uses the item (or a paper links the question), says why
 * it cannot be deleted yet instead of offering the button. Frozen records and issued result
 * sheets are kept.
 */
export function DeleteContentDialog({
  kind,
  id,
  name,
  onClose,
  onDeleted,
}: {
  kind: Kind
  id: string
  name: string
  onClose: () => void
  onDeleted: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const allowed = useQuery({
    queryKey: ['deletion', kind, id],
    queryFn: () => check(kind, id),
    staleTime: 0,
    gcTime: 0,
  })
  const words = WORDS[kind]

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } =
        kind === 'question'
          ? await api.DELETE('/api/v1/questions/{question_id}', {
              params: { path: { question_id: id } },
            })
          : await api.DELETE('/api/v1/blueprints/{blueprint_id}', {
              params: { path: { blueprint_id: id } },
            })
      if (response.ok) await onDeleted()
      else setError(problemOf(err, 'Could not delete it.').message)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  const blockers = allowed.data?.blockers ?? []
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Delete for good"
      title={words.title}
      description={name}
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>
            {allowed.data?.allowed ? 'Cancel' : 'Close'}
          </CancelButton>
          {allowed.data?.allowed && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void remove()}
              className="rounded-xl bg-red-600 px-4 py-2 text-xs font-bold text-white hover:bg-red-500 disabled:opacity-60"
            >
              {busy ? 'Deleting…' : 'Delete permanently'}
            </button>
          )}
        </div>
      }
    >
      <div className="space-y-3 text-xs text-gray-300">
        {allowed.isPending && <p>Checking whether it can be deleted…</p>}
        {allowed.isError && (
          <p role="alert" className="text-red-300">
            Could not check whether it can be deleted.
          </p>
        )}
        {allowed.data && !allowed.data.allowed && (
          <>
            <p>
              It cannot be deleted yet. Content is deleted only after the Governor has frozen every
              unit that uses it:
            </p>
            <ul aria-label="Why not yet" className="list-disc space-y-1 pl-5 text-amber-200">
              {blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </>
        )}
        {allowed.data?.allowed && (
          <p>
            This removes {words.gone}, with their files. It cannot be undone. Frozen results and the
            result sheets already issued are kept.
          </p>
        )}
        {error && (
          <p role="alert" className="text-red-300">
            {error}
          </p>
        )}
      </div>
    </Modal>
  )
}
