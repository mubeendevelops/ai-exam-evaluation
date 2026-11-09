import { useQueryClient } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { CancelButton, SubmitInDialog } from '../../pages/admin/controls'
import { starterSchema } from '../../lib/blueprint'
import { FormError } from '../public/fields'
import type { Blueprint } from '../schema/saved'
import { SelectInput, TextArea, TextInput } from '../schema/controls'
import { Modal, useToast } from '../ui'
import { useSubjects, type SchemaCard } from './schemas'

interface Props {
  /** The schema to clone; none: Add New Schema. */
  source?: SchemaCard
  batches: string[]
  onClose: () => void
  /** The saved schema: the new one (Add) or the copy (Clone). */
  onSaved: (blueprint: Blueprint) => void | Promise<void>
}

/**
 * Add / Clone Schema (mock modal 7): name, subject (Add only: a copy keeps its source's),
 * target class or batch and the rubric weightage description. Add saves a starter schema (one
 * section of five 2-mark questions) that the SME then shapes in the designer; Clone makes a new
 * schema, not a version (D231).
 */
export function SchemaDialog({ source, batches, onClose, onSaved }: Props) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const subjects = useSubjects()
  const batchList = useId()
  const [title, setTitle] = useState(source ? `${source.title} (copy)`.slice(0, 200) : '')
  const [subjectId, setSubjectId] = useState('')
  const [batch, setBatch] = useState(source?.target_batch ?? '')
  const [note, setNote] = useState(source?.weightage_note ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const live = (subjects.data ?? []).filter((s) => !s.retired)
  const subject = live.find((s) => s.id === subjectId)
  const ready = title.trim() !== '' && (source !== undefined || subject !== undefined)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = source
        ? await api.POST('/api/v1/blueprints/{blueprint_id}/clone', {
            params: { path: { blueprint_id: source.id } },
            body: { title: title.trim(), target_batch: batch.trim(), weightage_note: note.trim() },
          })
        : await api.POST('/api/v1/blueprints', {
            body: starterSchema({
              title: title.trim(),
              subjectId,
              courseCode: subject?.code ?? '',
              targetBatch: batch.trim(),
              weightageNote: note.trim(),
            }) as unknown as Record<string, unknown>,
          })
      if (!data) {
        setError(problemOf(err, 'Could not save the schema.').message)
        return
      }
      await queryClient.invalidateQueries({ queryKey: ['schemas'] })
      toast.show(source ? `${data.title} made.` : `${data.title} saved: shape its sections now.`)
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
      eyebrow={source ? 'Clone Schema' : 'Add New Schema'}
      title={source ? `Clone ${source.title}` : 'Add New Schema Blueprint'}
      description="Define the marking scheme and taxonomy ratios."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="schema-form" busy={busy} disabled={!ready}>
            {source ? 'Save copy' : 'Save Schema'}
          </SubmitInDialog>
        </div>
      }
    >
      <form id="schema-form" onSubmit={submit} className="space-y-4">
        <TextInput
          label="Schema Name"
          value={title}
          required
          maxLength={200}
          placeholder="e.g. Advanced Quantum Mechanics Matrix"
          onChange={(e) => setTitle(e.target.value)}
        />
        {source ? (
          <p className="text-xs text-gray-400">
            Subject: <span className="text-gray-200">{source.subject_name ?? 'as the source'}</span>{' '}
            (a copy keeps its subject).
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
            onChange={(e) => setSubjectId(e.target.value)}
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
          label="Target Class / Batch"
          value={batch}
          maxLength={100}
          list={batches.length > 0 ? batchList : undefined}
          placeholder="2024 batch, Class 12…"
          onChange={(e) => setBatch(e.target.value)}
        />
        {batches.length > 0 && (
          <datalist id={batchList}>
            {batches.map((b) => (
              <option key={b} value={b} />
            ))}
          </datalist>
        )}
        <TextArea
          label="Rubric Weightage Description"
          value={note}
          rows={3}
          maxLength={1000}
          placeholder="Define conceptual vs analytical weightages…"
          onChange={(e) => setNote(e.target.value)}
        />
        <FormError message={error} />
      </form>
    </Modal>
  )
}
