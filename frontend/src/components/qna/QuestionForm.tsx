import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import type { Seed } from '../../lib/questionSeed'
import { fromBody, toBody, type CriterionForm } from '../../lib/rubric'
import { FormError } from '../public/fields'
import { SubjectPicker } from '../schema/SubjectPicker'
import { SelectInput, TextArea, TextInput } from '../schema/controls'
import { RubricEditor } from './RubricEditor'
import { UsageWarning } from './UsageWarning'
import { fetchUsage, type QuestionUsage } from './usage'

type Question = components['schemas']['QuestionOut']
type Difficulty = components['schemas']['Difficulty']

export const DIFFICULTIES: { value: Difficulty; label: string }[] = [
  { value: 'easy', label: 'Easy' },
  { value: 'medium', label: 'Medium' },
  { value: 'hard', label: 'Hard' },
]

interface Props {
  /** The question being edited; none = a new question. */
  question?: Question
  /** A new question's start: the subject and marks (a paper slot's), or a copy (D165). */
  seed?: Seed
  /** Called with the saved question's id and whether it is new. */
  onSaved: (saved: { id: string; created: boolean }) => void
}

/**
 * The question as one form (P35, replaces the New / Edit modal): text, subject, topic,
 * difficulty, marks, code, the first reference answer of a new question, and the rubric.
 * Saving a question that papers link asks first (D223): the papers and open units it reaches.
 */
export function QuestionForm({ question, seed, onSaved }: Props) {
  const queryClient = useQueryClient()
  const editing = question !== undefined
  const [text, setText] = useState(question?.text ?? seed?.text ?? '')
  const [subjectId, setSubjectId] = useState(question?.subject_id ?? seed?.subjectId ?? '')
  const [category, setCategory] = useState(question?.category ?? seed?.category ?? '')
  const [difficulty, setDifficulty] = useState<Difficulty>(
    question?.difficulty ?? seed?.difficulty ?? 'medium',
  )
  const [marks, setMarks] = useState(question ? String(question.max_marks) : (seed?.marks ?? ''))
  const [code, setCode] = useState(question?.code ?? '')
  const [answer, setAnswer] = useState(seed?.answer ?? '')
  const [criteria, setCriteria] = useState<CriterionForm[]>(
    () => question?.rubric.criteria.map((c) => fromBody(c.criterion)) ?? seed?.criteria ?? [],
  )
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [usage, setUsage] = useState<QuestionUsage | null>(null)

  const topics = useQuery({
    queryKey: ['question-topics', 'all'],
    queryFn: async () => (await api.GET('/api/v1/question-topics')).data ?? [],
  })

  const diagrams = (question?.diagrams ?? []).map((d) => ({ id: d.id, name: d.name }))

  async function save() {
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
        queryClient.invalidateQueries({ queryKey: ['question-usage', data.id] }),
      ])
      onSaved({ id: data.id, created: !question })
    } catch {
      setProblem(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
      setUsage(null)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!question) {
      await save()
      return
    }
    // D223: a question that papers link reaches all of them; say which, then save.
    setBusy(true)
    setProblem(null)
    try {
      const found = await fetchUsage(queryClient, question.id)
      if (found.papers.length === 0) {
        await save()
        return
      }
      setUsage(found)
    } catch {
      setProblem('Could not check which papers use this question. Try again.')
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
      {seed?.note && (
        <p
          role="note"
          className="rounded-lg border border-cyan-500/30 bg-cyan-950/30 p-2.5 text-xs text-cyan-100"
        >
          {seed.note}
        </p>
      )}
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

      <section aria-label="Rubric" className="space-y-2">
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
      </section>

      <FormError message={problem} />
      <div className="flex justify-end gap-2 border-t border-gray-800 pt-4">
        <button
          type="submit"
          disabled={busy}
          className="rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 px-5 py-2 text-xs font-semibold text-white disabled:opacity-50"
        >
          {busy ? 'Saving…' : 'Save Question'}
        </button>
      </div>
      {usage && question && (
        <UsageWarning
          code={question.code}
          usage={usage}
          busy={busy}
          marks={
            Number(marks) !== question.max_marks
              ? { from: question.max_marks, to: Number(marks) }
              : undefined
          }
          onConfirm={() => void save()}
          onCancel={() => setUsage(null)}
        />
      )}
    </form>
  )
}
