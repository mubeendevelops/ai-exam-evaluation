import { useState } from 'react'
import { uid, type FormPart, type FormQuestion, type FormStep } from '../../lib/blueprint'
import { compactInputClass, IconButton, SmallButton } from './controls'

interface RowProps {
  question: FormQuestion
  onChange: (next: FormQuestion) => void
  onRemove?: () => void
  /** Accessible-name prefix that is stable while the label is being edited, e.g. "Section 1 item 3". */
  name: string
}

function sum(values: string[]): number {
  return Math.round(values.reduce((a, v) => a + (Number(v) || 0), 0) * 100) / 100
}

export function StepsEditor({
  steps,
  onChange,
  name,
}: {
  steps: FormStep[]
  onChange: (next: FormStep[]) => void
  name: string
}) {
  const update = (key: string, patch: Partial<FormStep>) =>
    onChange(steps.map((s) => (s.key === key ? { ...s, ...patch } : s)))
  return (
    <div className="space-y-1.5">
      {steps.map((step, i) => (
        <div key={step.key} className="flex items-center gap-2">
          <input
            aria-label={`${name} step ${i + 1} name`}
            className={compactInputClass}
            value={step.label}
            placeholder="Formula, substitution, answer…"
            onChange={(e) => update(step.key, { label: e.target.value })}
          />
          <input
            aria-label={`${name} step ${i + 1} marks`}
            className={`${compactInputClass} w-20 text-center font-bold text-emerald-400`}
            inputMode="decimal"
            value={step.marks}
            onChange={(e) => update(step.key, { marks: e.target.value })}
          />
          <IconButton
            label={`Remove ${name} step ${i + 1}`}
            icon="fa-solid fa-trash-can"
            tone="text-rose-400 hover:text-rose-300"
            onClick={() => onChange(steps.filter((s) => s.key !== step.key))}
          />
        </div>
      ))}
      <SmallButton
        icon="fa-solid fa-plus"
        tone="gray"
        onClick={() =>
          onChange([...steps, { key: uid(), label: `Step ${steps.length + 1}`, marks: '1' }])
        }
      >
        Add step
      </SmallButton>
    </div>
  )
}

function PartsEditor({
  question,
  onChange,
  name,
}: {
  question: FormQuestion
  onChange: (next: FormQuestion) => void
  name: string
}) {
  const setParts = (parts: FormPart[]) => onChange({ ...question, parts })
  const update = (key: string, patch: Partial<FormPart>) =>
    setParts(question.parts.map((p) => (p.key === key ? { ...p, ...patch } : p)))
  const total = sum(question.parts.map((p) => p.marks))
  const matches = total === (Number(question.marks) || 0)

  function add() {
    const label = String.fromCharCode(97 + (question.parts.length % 26))
    const first = question.parts.length === 0
    const part: FormPart = {
      key: uid(),
      label,
      marks: first ? question.marks : '1',
      questionId: '',
      steps: [],
    }
    // Links and steps move to the sub-parts: the question itself is no longer one leaf.
    onChange({ ...question, parts: [...question.parts, part], questionId: '', steps: [] })
  }

  return (
    <div className="space-y-2">
      {question.parts.map((part, i) => (
        <div key={part.key} className="space-y-1.5 rounded-lg border border-gray-800 p-2">
          <div className="flex items-center gap-2">
            <input
              aria-label={`${name} part ${i + 1} label`}
              className={`${compactInputClass} w-14 text-center`}
              value={part.label}
              onChange={(e) => update(part.key, { label: e.target.value })}
            />
            <input
              aria-label={`${name} part ${i + 1} marks`}
              className={`${compactInputClass} w-20 text-center font-bold text-emerald-400`}
              inputMode="decimal"
              value={part.marks}
              onChange={(e) => update(part.key, { marks: e.target.value })}
            />
            <input
              aria-label={`${name} part ${i + 1} question ID`}
              className={`${compactInputClass} grow font-mono`}
              placeholder="Question ID (optional)"
              value={part.questionId}
              onChange={(e) => update(part.key, { questionId: e.target.value })}
            />
            <IconButton
              label={`Remove ${name} part ${i + 1}`}
              icon="fa-solid fa-trash-can"
              tone="text-rose-400 hover:text-rose-300"
              onClick={() => setParts(question.parts.filter((p) => p.key !== part.key))}
            />
          </div>
          {part.steps.length > 0 && (
            <StepsEditor
              name={`${name} part ${i + 1}`}
              steps={part.steps}
              onChange={(steps) => update(part.key, { steps })}
            />
          )}
          {part.steps.length === 0 && (
            <SmallButton
              icon="fa-solid fa-list-check"
              tone="gray"
              onClick={() =>
                update(part.key, { steps: [{ key: uid(), label: 'Step 1', marks: part.marks }] })
              }
            >
              Split part {part.label} into steps
            </SmallButton>
          )}
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <SmallButton icon="fa-solid fa-plus" onClick={add}>
          Add sub-part
        </SmallButton>
        {question.parts.length > 0 && (
          <span className={`text-[11px] ${matches ? 'text-emerald-400' : 'text-amber-400'}`}>
            Sub-parts add up to {total} of {question.marks || 0} marks
          </span>
        )}
      </div>
    </div>
  )
}

/** One numbered question: number, marks, and (behind "Details") question link, sub-parts, steps. */
export function QuestionRow({ question, onChange, onRemove, name }: RowProps) {
  const [open, setOpen] = useState(false)
  const set = (patch: Partial<FormQuestion>) => onChange({ ...question, ...patch })
  const hasParts = question.parts.length > 0
  const notes = [
    hasParts ? `${question.parts.length} sub-parts` : '',
    question.steps.length > 0 ? `${question.steps.length} steps` : '',
    !hasParts && question.questionId.trim() ? 'linked' : '',
  ].filter(Boolean)

  return (
    <div className="rounded-xl border border-gray-800/80 bg-gray-950/40 p-2">
      <div className="flex items-center gap-2">
        <input
          aria-label={`${name} number`}
          className={`${compactInputClass} w-16 text-center font-bold text-cyan-300`}
          value={question.label}
          onChange={(e) => set({ label: e.target.value })}
        />
        <input
          aria-label={`${name} marks`}
          className={`${compactInputClass} w-20 text-center font-bold text-emerald-400`}
          inputMode="decimal"
          value={question.marks}
          onChange={(e) => set({ marks: e.target.value })}
        />
        <span className="grow truncate text-[11px] text-gray-400">{notes.join(' · ')}</span>
        <IconButton
          label={`${open ? 'Hide' : 'Show'} details of ${name}`}
          icon={`fa-solid ${open ? 'fa-chevron-up' : 'fa-sliders'}`}
          pressed={open}
          onClick={() => setOpen(!open)}
        />
        {onRemove && (
          <IconButton
            label={`Remove ${name}`}
            icon="fa-solid fa-trash-can"
            tone="text-rose-400 hover:text-rose-300"
            onClick={onRemove}
          />
        )}
      </div>
      {open && (
        <div className="mt-3 space-y-3 border-t border-gray-800 pt-3">
          <div>
            <p className="mb-1 text-[11px] font-semibold tracking-wider text-gray-300 uppercase">
              Question link
            </p>
            <input
              aria-label={`${name} question ID`}
              className={`${compactInputClass} font-mono`}
              placeholder={hasParts ? 'Link each sub-part instead' : 'Question ID (optional)'}
              disabled={hasParts}
              value={question.questionId}
              onChange={(e) => set({ questionId: e.target.value })}
            />
          </div>
          <div>
            <p className="mb-1 text-[11px] font-semibold tracking-wider text-gray-300 uppercase">
              Sub-parts with their own marks
            </p>
            <PartsEditor question={question} onChange={onChange} name={name} />
          </div>
          {!hasParts && (
            <div>
              <p className="mb-1 text-[11px] font-semibold tracking-wider text-gray-300 uppercase">
                Step marks
              </p>
              <StepsEditor
                name={name}
                steps={question.steps}
                onChange={(steps) => set({ steps })}
              />
              {question.steps.length > 0 && (
                <p
                  className={`mt-1 text-[11px] ${
                    sum(question.steps.map((s) => s.marks)) === (Number(question.marks) || 0)
                      ? 'text-emerald-400'
                      : 'text-amber-400'
                  }`}
                >
                  Steps add up to {sum(question.steps.map((s) => s.marks))} of {question.marks || 0}{' '}
                  marks
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
