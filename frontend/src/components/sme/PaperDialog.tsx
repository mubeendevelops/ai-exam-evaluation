import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { CancelButton, SubmitInDialog } from '../../pages/admin/controls'
import { FormError } from '../public/fields'
import type { Blueprint } from '../schema/saved'
import { SelectInput, TextInput } from '../schema/controls'
import { Modal, useToast } from '../ui'
import type { PaperCard } from './papers'
import { useSubjects } from './schemas'

interface Props {
  /** The paper to clone; none: Create Question Paper. */
  source?: PaperCard
  descriptors: string[]
  onClose: () => void
  /** The saved paper: the new one or the copy. */
  onSaved: (paper: Blueprint) => void | Promise<void>
}

/** The schemas of one subject, for "Start from schema". */
function useSubjectSchemas(subjectId: string) {
  return useQuery({
    queryKey: ['blueprints', 'schema'],
    enabled: subjectId !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/blueprints', {
        params: { query: { kind: 'schema' } },
      })
      if (!data) throw new Error('schemas unavailable')
      return data
    },
    select: (all) => all.filter((s) => s.subject_id === subjectId),
  })
}

/**
 * Create / Clone Question Paper (mock modal 6, P34): title, subject (Create only: a copy keeps
 * its source's) and descriptor, with the descriptors in use as suggestions. Create may start
 * from a schema of the subject; otherwise a starter paper (section A, five 2-mark questions).
 * Clone makes a new paper, not a version, without the exam code.
 */
export function PaperDialog({ source, descriptors, onClose, onSaved }: Props) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const subjects = useSubjects()
  const suggestions = useId()
  const [title, setTitle] = useState(source ? `${source.title} (Copy)`.slice(0, 200) : '')
  const [subjectId, setSubjectId] = useState('')
  const [descriptor, setDescriptor] = useState(source?.descriptor ?? '')
  const [schemaId, setSchemaId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const schemas = useSubjectSchemas(source ? '' : subjectId)
  const live = (subjects.data ?? []).filter((s) => !s.retired)
  const ready = title.trim() !== '' && (source !== undefined || subjectId !== '')

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = source
        ? await api.POST('/api/v1/blueprints/{blueprint_id}/clone', {
            params: { path: { blueprint_id: source.id } },
            body: { title: title.trim(), descriptor: descriptor.trim() },
          })
        : await api.POST('/api/v1/papers', {
            body: {
              title: title.trim(),
              subject_id: subjectId,
              descriptor: descriptor.trim(),
              ...(schemaId ? { schema_id: schemaId } : {}),
            },
          })
      if (!data) {
        setError(problemOf(err, 'Could not save the paper.').message)
        return
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['papers'] }),
        queryClient.invalidateQueries({ queryKey: ['schemas'] }),
        queryClient.invalidateQueries({ queryKey: ['blueprints'] }),
      ])
      toast.show(source ? `${data.title} made.` : `${data.title} saved: link its questions now.`)
      await onSaved(data)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={source ? 'Clone Question Paper' : 'Create Question Paper'}
      title="Create / Clone Question Paper"
      description="Configure the paper title, subject and descriptor."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="paper-form" busy={busy} disabled={!ready}>
            {source ? 'Save copy' : 'Save & Generate Paper'}
          </SubmitInDialog>
        </div>
      }
    >
      <form id="paper-form" onSubmit={submit} className="space-y-4">
        <TextInput
          label="Question Paper Name"
          value={title}
          required
          maxLength={200}
          placeholder="e.g. Physics Mid Term 2026 Batch Paper"
          onChange={(e) => setTitle(e.target.value)}
        />
        {source ? (
          <p className="text-xs text-gray-400">
            Subject: <span className="text-gray-200">{source.subject_name ?? 'as the source'}</span>{' '}
            (a copy keeps its subject and structure; it gets no exam code).
          </p>
        ) : (
          <SelectInput
            label="Subject"
            value={subjectId}
            required
            hint={
              subjects.isError
                ? 'Could not load your subjects.'
                : subjects.data && live.length === 0
                  ? 'You have no subjects yet: ask your admin to grant you one.'
                  : undefined
            }
            onChange={(e) => {
              setSubjectId(e.target.value)
              setSchemaId('')
            }}
          >
            <option value="">Select a subject…</option>
            {live.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.code})
              </option>
            ))}
          </SelectInput>
        )}
        <TextInput
          label="Popular Descriptor"
          value={descriptor}
          maxLength={50}
          list={descriptors.length > 0 ? suggestions : undefined}
          placeholder="e.g. Mid term 2026 batch"
          onChange={(e) => setDescriptor(e.target.value)}
        />
        {descriptors.length > 0 && (
          <datalist id={suggestions}>
            {descriptors.map((d) => (
              <option key={d} value={d} />
            ))}
          </datalist>
        )}
        {!source && subjectId !== '' && (
          <SelectInput
            label="Start from schema (optional)"
            value={schemaId}
            hint="Its sections, choice rules and marks; otherwise five 2-mark questions to shape."
            onChange={(e) => setSchemaId(e.target.value)}
          >
            <option value="">No schema: a starter paper</option>
            {(schemas.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} ({s.total_marks} marks)
              </option>
            ))}
          </SelectInput>
        )}
        <FormError message={error} />
      </form>
    </Modal>
  )
}
