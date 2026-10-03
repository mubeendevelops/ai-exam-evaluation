import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import { fromBody, toBody, type CriterionForm } from '../../lib/rubric'
import { FormError } from '../public/fields'
import { SmallButton, TextArea } from '../schema/controls'
import { Modal } from '../ui'
import { RubricEditor } from './RubricEditor'

type Question = components['schemas']['QuestionOut']
type Answer = components['schemas']['ReferenceAnswerOut']

/** Runs a save, shows the server's reason on a refusal, refreshes the question on success. */
function useSaver(questionId: string, onDone: () => void) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function run(save: () => Promise<{ ok: boolean; error?: unknown }>, fallback: string) {
    setBusy(true)
    setProblem(null)
    try {
      const result = await save()
      if (!result.ok) {
        const p = problemOf(result.error, fallback)
        setProblem([p.message, ...p.reasons].join(' '))
        return
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['question', questionId] }),
        queryClient.invalidateQueries({ queryKey: ['questions'] }),
      ])
      onDone()
    } catch {
      setProblem(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }
  return { busy, problem, run }
}

function Footer({ busy, onClose, label }: { busy: boolean; onClose: () => void; label: string }) {
  return (
    <div className="flex justify-end gap-2 border-t border-gray-800 pt-4">
      <SmallButton tone="gray" onClick={onClose}>
        Cancel
      </SmallButton>
      <button
        type="submit"
        disabled={busy}
        className="rounded-xl bg-purple-600 px-5 py-2 text-xs font-semibold text-white hover:bg-purple-500 disabled:opacity-50"
      >
        {busy ? 'Saving…' : label}
      </button>
    </div>
  )
}

// ---- reference answer -----------------------------------------------------------------------

interface AnswerProps {
  open: boolean
  onClose: () => void
  question: Question
  /** The answer being edited; none = a new one. */
  answer?: Answer
}

export function AnswerModal({ open, onClose, question, answer }: AnswerProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={question.code}
      title={answer ? 'Edit reference answer' : 'Add reference answer'}
      description="Written by faculty: it holds no student data."
    >
      <AnswerForm key={answer?.id ?? 'new'} question={question} answer={answer} onClose={onClose} />
    </Modal>
  )
}

function AnswerForm({ question, answer, onClose }: Omit<AnswerProps, 'open'>) {
  const [text, setText] = useState(answer?.text ?? '')
  const [guidance, setGuidance] = useState(answer?.guidance_only ?? false)
  const { busy, problem, run } = useSaver(question.id, onClose)

  function submit(event: FormEvent) {
    event.preventDefault()
    const body = { text: text.trim(), guidance_only: guidance }
    void run(async () => {
      const result = answer
        ? await api.PUT('/api/v1/questions/{question_id}/reference-answers/{answer_id}', {
            params: { path: { question_id: question.id, answer_id: answer.id } },
            body,
          })
        : await api.POST('/api/v1/questions/{question_id}/reference-answers', {
            params: { path: { question_id: question.id } },
            body,
          })
      return { ok: result.response.ok, error: result.error }
    }, 'Could not save the answer.')
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Reference answer">
      <TextArea
        label="Answer text"
        required
        rows={5}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <label className="flex items-start gap-2 text-xs text-gray-200">
        <input
          type="checkbox"
          checked={guidance}
          onChange={(e) => setGuidance(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-purple-500"
        />
        <span>
          <strong>Guidance only.</strong> This is not a usable model answer: the AI gives no score
          and the teacher marks by hand.
        </span>
      </label>
      <FormError message={problem} />
      <Footer busy={busy} onClose={onClose} label="Save answer" />
    </form>
  )
}

// ---- glossary -------------------------------------------------------------------------------

export function GlossaryModal({
  open,
  onClose,
  question,
}: {
  open: boolean
  onClose: () => void
  question: Question
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={question.code}
      title="Edit glossary"
      description="Terms the answer is expected to use. Labels of the reference diagrams are added automatically."
    >
      <GlossaryForm question={question} onClose={onClose} />
    </Modal>
  )
}

function GlossaryForm({ question, onClose }: { question: Question; onClose: () => void }) {
  const [text, setText] = useState(question.glossary.teacher_terms.join('\n'))
  const { busy, problem, run } = useSaver(question.id, onClose)

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      const result = await api.PUT('/api/v1/questions/{question_id}/glossary', {
        params: { path: { question_id: question.id } },
        body: { terms: text.split('\n') },
      })
      return { ok: result.response.ok, error: result.error }
    }, 'Could not save the glossary.')
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Glossary">
      <TextArea
        label="Terms, one per line"
        rows={8}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={'focal length\nconvex lens'}
      />
      {question.glossary.reference_labels.length > 0 && (
        <p className="text-[11px] text-gray-400">
          From the reference diagrams: {question.glossary.reference_labels.join(', ')}
        </p>
      )}
      <FormError message={problem} />
      <Footer busy={busy} onClose={onClose} label="Save glossary" />
    </form>
  )
}

// ---- rubric ---------------------------------------------------------------------------------

export function RubricModal({
  open,
  onClose,
  question,
}: {
  open: boolean
  onClose: () => void
  question: Question
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="max-w-3xl"
      eyebrow={question.code}
      title="Edit rubric"
      description={`The weights add up to the question's ${question.max_marks} marks.`}
    >
      <RubricForm
        key={`${question.id}-${question.version}`}
        question={question}
        onClose={onClose}
      />
    </Modal>
  )
}

function RubricForm({ question, onClose }: { question: Question; onClose: () => void }) {
  const [criteria, setCriteria] = useState<CriterionForm[]>(() =>
    question.rubric.criteria.map((c) => fromBody(c.criterion)),
  )
  const { busy, problem, run } = useSaver(question.id, onClose)

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      const result = await api.PUT('/api/v1/questions/{question_id}/rubric', {
        params: { path: { question_id: question.id } },
        body: { criteria: criteria.map(toBody) },
      })
      return { ok: result.response.ok, error: result.error }
    }, 'Could not save the rubric.')
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Rubric">
      <RubricEditor
        criteria={criteria}
        onChange={setCriteria}
        maxMarks={String(question.max_marks)}
        diagrams={question.diagrams.map((d) => ({ id: d.id, name: d.name }))}
      />
      <FormError message={problem} />
      <Footer busy={busy} onClose={onClose} label="Save rubric" />
    </form>
  )
}
