import { useState } from 'react'
import {
  METHODS,
  countedItems,
  newQuestion,
  sectionMax,
  uid,
  type FormItem,
  type FormQuestion,
  type FormSection,
  type Method,
} from '../../lib/blueprint'
import { compactInputClass, IconButton, SmallButton } from './controls'
import { QuestionRow } from './QuestionRow'

interface Props {
  section: FormSection
  index: number
  /** Number the next added question gets. */
  nextNumber: number
  onChange: (next: FormSection) => void
  onRemove: () => void
  onFill: (count: number, marks: string) => void
}

function Caption({ children }: { children: string }) {
  return (
    <span className="mb-1 block text-[11px] font-semibold text-gray-400 uppercase">{children}</span>
  )
}

/** One section of the Section Breakdown Architecture: its choice rule, method and questions. */
export function SectionCard({ section, index, nextNumber, onChange, onRemove, onFill }: Props) {
  const name = `Section ${index + 1}`
  const [count, setCount] = useState('')
  const [marks, setMarks] = useState('')
  const set = (patch: Partial<FormSection>) => onChange({ ...section, ...patch })
  const items = section.items
  const counted = countedItems(section)

  const setItem = (key: string, next: FormItem) =>
    set({ items: items.map((i) => (i.key === key ? next : i)) })
  const removeItem = (key: string) => set({ items: items.filter((i) => i.key !== key) })

  const fresh = (offset = 0): FormQuestion =>
    newQuestion(String(nextNumber + offset), lastMarks(items))

  function addQuestion() {
    set({ items: [...items, { key: uid(), kind: 'question', question: fresh() }] })
  }
  function addOrPair() {
    set({
      items: [...items, { key: uid(), kind: 'or', alternatives: [fresh(0), fresh(1)] }],
    })
  }
  function applyFill() {
    const n = Math.trunc(Number(count))
    if (n >= 1 && Number(marks) > 0) {
      onFill(n, marks)
      setCount('')
      setMarks('')
    }
  }

  return (
    <section
      aria-label={name}
      className="space-y-4 rounded-2xl border border-gray-800 bg-gray-900/40 p-4"
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-12">
        <div className="sm:col-span-2">
          <Caption>Section</Caption>
          <input
            aria-label={`${name} name`}
            className={`${compactInputClass} text-center font-bold`}
            value={section.label}
            onChange={(e) => set({ label: e.target.value })}
          />
        </div>
        <div className="col-span-2 sm:col-span-4">
          <Caption>Title (optional)</Caption>
          <input
            aria-label={`${name} title`}
            className={compactInputClass}
            value={section.title}
            placeholder="Short answers"
            onChange={(e) => set({ title: e.target.value })}
          />
        </div>
        <div className="col-span-2 sm:col-span-4">
          <Caption>Evaluation method</Caption>
          <select
            aria-label={`${name} evaluation method`}
            className={compactInputClass}
            value={section.method}
            onChange={(e) => set({ method: e.target.value as Method })}
          >
            {METHODS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        <div className="col-span-2 flex items-end justify-end sm:col-span-2">
          <IconButton
            label={`Remove ${name}`}
            icon="fa-solid fa-trash-can"
            tone="text-rose-400 hover:text-rose-300"
            onClick={onRemove}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-12">
        <div className="sm:col-span-4">
          <Caption>Students answer</Caption>
          <select
            aria-label={`${name} choice rule`}
            className={compactInputClass}
            value={section.rule}
            onChange={(e) => {
              const rule = e.target.value === 'any' ? 'any' : 'all'
              set({ rule, n: rule === 'any' && !Number(section.n) ? '1' : section.n })
            }}
          >
            <option value="all">All questions</option>
            <option value="any">Any N of M (best N count)</option>
          </select>
        </div>
        {section.rule === 'any' && (
          <div className="sm:col-span-2">
            <Caption>N</Caption>
            <input
              aria-label={`${name} N`}
              className={`${compactInputClass} text-center font-bold text-cyan-300`}
              inputMode="numeric"
              value={section.n}
              onChange={(e) => set({ n: e.target.value })}
            />
          </div>
        )}
        <div className="col-span-2 flex items-end sm:col-span-6 sm:justify-end">
          <p className="text-right text-xs text-gray-300">
            {section.rule === 'any'
              ? `Best ${counted} of ${items.length} count`
              : `All ${items.length} count`}
            {' · '}
            <strong className="text-white">{sectionMax(section)} Marks</strong>
          </p>
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-end gap-2 rounded-xl border border-gray-800 bg-gray-950/40 p-2">
          <div className="w-28">
            <Caption>No. of questions</Caption>
            <input
              aria-label={`${name} question count`}
              className={`${compactInputClass} text-center font-bold text-cyan-300`}
              inputMode="numeric"
              value={count}
              placeholder={String(items.length)}
              onChange={(e) => setCount(e.target.value)}
            />
          </div>
          <div className="w-32">
            <Caption>Marks per question</Caption>
            <input
              aria-label={`${name} marks per question`}
              className={`${compactInputClass} text-center font-bold text-emerald-400`}
              inputMode="decimal"
              value={marks}
              placeholder="2"
              onChange={(e) => setMarks(e.target.value)}
            />
          </div>
          <SmallButton icon="fa-solid fa-table-list" tone="purple" onClick={applyFill}>
            Fill {name.toLowerCase()}
          </SmallButton>
          <p className="grow text-right text-[10px] text-gray-400">
            Replaces the questions below with this many plain questions.
          </p>
        </div>

        {items.length > 0 && (
          <div
            aria-hidden="true"
            className="flex gap-2 px-2 text-[10px] font-semibold text-gray-500 uppercase"
          >
            <span className="w-16 text-center">No.</span>
            <span className="w-20 text-center">Marks</span>
          </div>
        )}

        {items.map((item, i) =>
          item.kind === 'question' ? (
            <QuestionRow
              key={item.key}
              name={`${name} item ${i + 1}`}
              question={item.question}
              onChange={(question) => setItem(item.key, { ...item, question })}
              onRemove={() => removeItem(item.key)}
            />
          ) : (
            <div
              key={item.key}
              role="group"
              aria-label={`${name} item ${i + 1} OR pair`}
              className="space-y-2 rounded-xl border border-purple-500/30 bg-purple-950/20 p-2"
            >
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold tracking-wider text-purple-300 uppercase">
                  OR pair: one counts, the higher score
                </span>
                <IconButton
                  label={`Remove ${name} item ${i + 1}`}
                  icon="fa-solid fa-trash-can"
                  tone="text-rose-400 hover:text-rose-300"
                  onClick={() => removeItem(item.key)}
                />
              </div>
              {item.alternatives.map((alt, k) => (
                <QuestionRow
                  key={alt.key}
                  name={`${name} item ${i + 1} alternative ${k + 1}`}
                  question={alt}
                  onChange={(next) =>
                    setItem(item.key, {
                      ...item,
                      alternatives: item.alternatives.map((a) => (a.key === alt.key ? next : a)),
                    })
                  }
                  onRemove={
                    item.alternatives.length > 2
                      ? () =>
                          setItem(item.key, {
                            ...item,
                            alternatives: item.alternatives.filter((a) => a.key !== alt.key),
                          })
                      : undefined
                  }
                />
              ))}
              <SmallButton
                icon="fa-solid fa-plus"
                tone="gray"
                onClick={() =>
                  setItem(item.key, {
                    ...item,
                    alternatives: [
                      ...item.alternatives,
                      newQuestion(
                        String(nextNumber + item.alternatives.length),
                        item.alternatives[0]?.marks ?? '2',
                      ),
                    ],
                  })
                }
              >
                Add alternative
              </SmallButton>
            </div>
          ),
        )}
        {items.length === 0 && (
          <p className="text-xs text-amber-400">This section has no questions yet.</p>
        )}
        <div className="flex flex-wrap gap-2">
          <SmallButton icon="fa-solid fa-plus" onClick={addQuestion}>
            Add question to {name.toLowerCase()}
          </SmallButton>
          <SmallButton icon="fa-solid fa-code-branch" tone="purple" onClick={addOrPair}>
            Add OR pair to {name.toLowerCase()}
          </SmallButton>
        </div>
      </div>
    </section>
  )
}

function lastMarks(items: FormItem[]): string {
  const last = items[items.length - 1]
  if (!last) return '2'
  return last.kind === 'or' ? (last.alternatives[0]?.marks ?? '2') : last.question.marks
}
