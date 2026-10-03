import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import { fromBody, toBody, type CriterionForm } from '../../lib/rubric'
import { FormError } from '../public/fields'
import { SubjectPicker } from '../schema/SubjectPicker'
import { SelectInput, SmallButton, TextArea, TextInput } from '../schema/controls'
import { Modal } from '../ui'
import { RubricEditor } from './RubricEditor'

type Question = components['schemas']['QuestionOut']
type Difficulty = components['schemas']['Difficulty']

export const DIFFICULTIES: { value: Difficulty; label: string }[] = [
  { value: 'easy', label: 'Easy' },
  { value: 'medium', label: 'Medium' },
  { value: 'hard', label: 'Hard' },
]

interface Props {
  open: boolean
  onClose: () => void
  /** The question being edited; none = a new question. */
  question?: Question
  /** Called with the saved question's id. */
  onSaved: (id: string) => void
}

/** "New Question" / "Edit question": text, subject, topic, difficulty, marks, code, key, rubric. */
export function QuestionModal({ open, onClose, question, onSaved }: Props) {
  // Remount the form for each question (and each opening), so it starts from the stored values.
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="max-w-3xl"
      eyebrow={question ? 'Edit question' : 'Question bank'}
      title={question ? `Edit ${question.code}` : 'Add New Question'}
      description="The question, its benchmark answer and the rubric the AI scores it by."
    >
      <QuestionForm
        key={question ? `${question.id}-${question.version}` : 'new'}
        question={question}
        onClose={onClose}
        onSaved={onSaved}
      />
    </Modal>
  )
}

function QuestionForm({
  question,
  onClose,
  onSaved,
}: Pick<Props, 'question' | 'onClose' | 'onSaved'>) {
  const queryClient = useQueryClient()
  const editing = question !== undefined
  const [text, setText] = useState(question?.text ?? '')
  const [subjectId, setSubjectId] = useState(question?.subject_id ?? '')
  const [category, setCategory] = useState(question?.category ?? '')
  const [difficulty, setDifficulty] = useState<Difficulty>(question?.difficulty ?? 'medium')
  const [marks, setMarks] = useState(question ? String(question.max_marks) : '')
  const [code, setCode] = useState(question?.code ?? '')
  const [answer, setAnswer] = useState('')
  const [criteria, setCriteria] = useState<CriterionForm[]>(
    () => question?.rubric.criteria.map((c) => fromBody(c.criterion)) ?? [],
  )
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const topics = useQuery({
    queryKey: ['question-topics', 'all'],
    queryFn: async () => (await api.GET('/api/v1/question-topics')).data ?? [],
  })

  const diagrams = (question?.diagrams ?? []).map((d) => ({ id: d.id, name: d.name }))

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    const shared = {
      code: code.trim(),
      text: text.trim(),
      max_marks: Number(marks) || (marks as unknown as number),
      difficulty,
      category: category.trim(),
    }
    const rubric = criteria.map(toBody)
    try {
      const { data, error, response } = question
        ? await api.PUT('/api/v1/questions/{question_id}', {
            params: { path: { question_id: question.id } },
            body: { ...shared, criteria: rubric },
          })
        : await api.POST('/api/v1/questions', {
            body: {
              ...shared,
              subject_id: subjectId,
              reference_answer: answer.trim() === '' ? null : answer.trim(),
              criteria: rubric,
            },
          })
      if (!response.ok || !data) {
        const p = problemOf(error, 'Could not save the question.')
        setProblem([p.message, ...p.reasons].join(' '))
        return
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['questions'] }),
        queryClient.invalidateQueries({ queryKey: ['question', data.id] }),
        queryClient.invalidateQueries({ queryKey: ['question-topics'] }),
      ])
      onSaved(data.id)
    } catch {
      setProblem(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-4"
      aria-label={editing ? 'Edit question' : 'New question'}
    >
      <TextArea
        label="Question Title / Text"
        required
        rows={3}
        placeholder="Enter question statement…"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        {editing ? (
          <TextInput
            label="Subject"
            value={question.subject_name ?? ''}
            disabled
            readOnly
            hint="A question stays in its subject; copy it to move it."
          />
        ) : (
          <SubjectPicker value={subjectId} onChange={setSubjectId} />
        )}
        <div>
          <TextInput
            label="Topic"
            list="topic-options"
            placeholder="e.g. Electromagnetism"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
          <datalist id="topic-options">
            {topics.data?.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
        </div>
        <SelectInput
          label="Difficulty"
          value={difficulty}
          onChange={(e) => setDifficulty(e.target.value as Difficulty)}
        >
          {DIFFICULTIES.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </SelectInput>
        <TextInput
          label="Max marks"
          required
          inputMode="decimal"
          placeholder="5"
          value={marks}
          onChange={(e) => setMarks(e.target.value)}
        />
        <TextInput
          label="Question code"
          required
          maxLength={40}
          placeholder="PHY-Q101"
          hint="Unique within your college."
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </div>

      {!editing && (
        <TextArea
          label="Reference answer (benchmark)"
          rows={3}
          placeholder="The model answer a good student would write."
          hint="Optional now; add more keys later. Keys are written by faculty and hold no student data."
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
        />
      )}

      <div className="space-y-2">
        <h3 className="text-xs font-bold tracking-wider text-cyan-400 uppercase">Rubric</h3>
        <p className="text-[11px] text-gray-400">
          Each criterion is scored by the simplest method that works for it; the marks of all
          criteria add up to the question's marks. Leave it empty for a guidance-only key.
        </p>
        <RubricEditor
          criteria={criteria}
          onChange={setCriteria}
          maxMarks={marks}
          diagrams={diagrams}
        />
      </div>

      <FormError message={problem} />
      <div className="flex justify-end gap-2 border-t border-gray-800 pt-4">
        <SmallButton tone="gray" onClick={onClose}>
          Cancel
        </SmallButton>
        <button
          type="submit"
          disabled={busy}
          className="rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 px-5 py-2 text-xs font-semibold text-white disabled:opacity-50"
        >
          {busy ? 'Saving…' : 'Save Question'}
        </button>
      </div>
    </form>
  )
}
