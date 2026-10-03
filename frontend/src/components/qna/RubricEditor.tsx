import {
  COMPONENTS,
  CRITERION_TYPES,
  evenWeights,
  newCriterion,
  weightTotal,
  type Component,
  type CriterionForm,
  type CriterionType,
} from '../../lib/rubric'
import { compactInputClass, IconButton, SmallButton } from '../schema/controls'

export interface DiagramChoice {
  id: string
  name: string
}

interface Props {
  criteria: CriterionForm[]
  onChange: (next: CriterionForm[]) => void
  /** The question's marks as typed; the weights must add up to it. */
  maxMarks: string
  /** Reference diagrams of the question (a diagram criterion points at one). */
  diagrams: DiagramChoice[]
}

function Caption({ children }: { children: string }) {
  return (
    <span className="mb-1 block text-[11px] font-semibold text-gray-400 uppercase">{children}</span>
  )
}

function Params({
  c,
  name,
  set,
  diagrams,
}: {
  c: CriterionForm
  name: string
  set: (patch: Partial<CriterionForm>) => void
  diagrams: DiagramChoice[]
}) {
  switch (c.type) {
    case 'list':
      return (
        <div className="grid gap-3 sm:grid-cols-12">
          <div className="sm:col-span-9">
            <Caption>Items, one per line (term | synonym, synonym)</Caption>
            <textarea
              aria-label={`${name} items`}
              rows={4}
              className={`${compactInputClass} resize-y font-mono`}
              placeholder={
                'President’s Rule | failure of constitutional machinery\nNational Emergency'
              }
              value={c.items}
              onChange={(e) => set({ items: e.target.value })}
            />
          </div>
          <div className="sm:col-span-3">
            <Caption>Required count</Caption>
            <input
              aria-label={`${name} required count`}
              className={`${compactInputClass} text-center font-bold text-cyan-300`}
              inputMode="numeric"
              value={c.required}
              onChange={(e) => set({ required: e.target.value })}
            />
            <p className="mt-1 text-[10px] text-gray-400">Credit = items named ÷ required.</p>
          </div>
        </div>
      )
    case 'numeric':
      return (
        <div className="grid gap-3 sm:grid-cols-12">
          <div className="sm:col-span-4">
            <Caption>Expected value</Caption>
            <input
              aria-label={`${name} expected value`}
              className={compactInputClass}
              inputMode="decimal"
              value={c.expected}
              onChange={(e) => set({ expected: e.target.value })}
            />
          </div>
          <div className="sm:col-span-4">
            <Caption>Tolerance (±)</Caption>
            <input
              aria-label={`${name} tolerance`}
              className={compactInputClass}
              inputMode="decimal"
              value={c.tolerance}
              onChange={(e) => set({ tolerance: e.target.value })}
            />
          </div>
          <div className="sm:col-span-4">
            <Caption>Unit (optional)</Caption>
            <input
              aria-label={`${name} unit`}
              className={compactInputClass}
              value={c.unit}
              onChange={(e) => set({ unit: e.target.value })}
            />
          </div>
        </div>
      )
    case 'semantic':
      return (
        <div>
          <Caption>Reference statement</Caption>
          <textarea
            aria-label={`${name} reference statement`}
            rows={2}
            className={`${compactInputClass} resize-y`}
            placeholder="One point the answer should make."
            value={c.statement}
            onChange={(e) => set({ statement: e.target.value })}
          />
        </div>
      )
    case 'diagram':
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Caption>Reference diagram</Caption>
            <select
              aria-label={`${name} reference diagram`}
              className={compactInputClass}
              value={c.diagramId}
              onChange={(e) => set({ diagramId: e.target.value })}
            >
              <option value="">
                {diagrams.length === 0 ? 'Upload a reference diagram first' : 'Select…'}
              </option>
              {diagrams.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Caption>Compared part</Caption>
            <select
              aria-label={`${name} compared part`}
              className={compactInputClass}
              value={c.component}
              onChange={(e) => set({ component: e.target.value as Component })}
            >
              {COMPONENTS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )
  }
}

/** Weighted criteria of one question: list, numeric, semantic and diagram. */
export function RubricEditor({ criteria, onChange, maxMarks, diagrams }: Props) {
  const total = weightTotal(criteria)
  const marks = Number(maxMarks) || 0
  const complete = criteria.length > 0 && total === marks

  const update = (key: string, patch: Partial<CriterionForm>) =>
    onChange(criteria.map((c) => (c.key === key ? { ...c, ...patch } : c)))

  function add(type: CriterionType) {
    // A first criterion takes all the marks; later ones take what is still unassigned.
    const left = Math.max(Math.round((marks - total) * 100) / 100, 0)
    onChange([...criteria, newCriterion(type, left > 0 ? String(left) : '')])
  }

  return (
    <div className="space-y-3">
      {criteria.map((c, i) => {
        const name = `Criterion ${i + 1}`
        return (
          <fieldset
            key={c.key}
            className="space-y-3 rounded-xl border border-gray-800 bg-gray-950/40 p-3"
          >
            <legend className="sr-only">{name}</legend>
            <div className="grid grid-cols-12 items-end gap-2">
              <div className="col-span-12 sm:col-span-3">
                <Caption>Type</Caption>
                <select
                  aria-label={`${name} type`}
                  className={compactInputClass}
                  value={c.type}
                  disabled={c.id !== null}
                  onChange={(e) => update(c.key, { type: e.target.value as CriterionType })}
                >
                  {CRITERION_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="col-span-8 sm:col-span-6">
                <Caption>Criterion</Caption>
                <input
                  aria-label={`${name} label`}
                  className={compactInputClass}
                  placeholder="What this criterion rewards"
                  value={c.label}
                  onChange={(e) => update(c.key, { label: e.target.value })}
                />
              </div>
              <div className="col-span-3 sm:col-span-2">
                <Caption>Marks</Caption>
                <input
                  aria-label={`${name} weight`}
                  className={`${compactInputClass} text-center font-bold text-emerald-400`}
                  inputMode="decimal"
                  value={c.weight}
                  onChange={(e) => update(c.key, { weight: e.target.value })}
                />
              </div>
              <div className="col-span-1 flex justify-end">
                <IconButton
                  label={`Remove ${name}`}
                  icon="fa-solid fa-trash-can"
                  tone="text-rose-400 hover:text-rose-300"
                  onClick={() => onChange(criteria.filter((x) => x.key !== c.key))}
                />
              </div>
            </div>
            <Params c={c} name={name} diagrams={diagrams} set={(patch) => update(c.key, patch)} />
          </fieldset>
        )
      })}

      <div className="flex flex-wrap items-center gap-2">
        {CRITERION_TYPES.map((t) => (
          <SmallButton
            key={t.value}
            icon="fa-solid fa-plus"
            tone="gray"
            onClick={() => add(t.value)}
          >
            {`Add ${t.label.toLowerCase()} criterion`}
          </SmallButton>
        ))}
        {criteria.length > 1 && marks > 0 && (
          <SmallButton
            icon="fa-solid fa-equals"
            tone="purple"
            onClick={() => {
              const weights = evenWeights(marks, criteria.length)
              onChange(criteria.map((c, i) => ({ ...c, weight: weights[i] ?? c.weight })))
            }}
          >
            Share the marks evenly
          </SmallButton>
        )}
      </div>

      <p
        role="status"
        className={`text-xs ${complete || criteria.length === 0 ? 'text-gray-300' : 'text-amber-400'}`}
      >
        {criteria.length === 0
          ? 'No criteria: the key is guidance only and the teacher marks by hand.'
          : `Weights add up to ${total} of ${marks} marks${complete ? '.' : ': they must match.'}`}
      </p>
    </div>
  )
}
