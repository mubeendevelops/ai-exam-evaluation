import { useState } from 'react'
import { questionEcho } from '../../lib/questionEcho'
import { isTextLine, type LineView, type SegmentView } from '../../lib/segments'
import { compactInputClass, SmallButton } from '../schema/controls'
import { Badge, type Tone } from '../ui'
import { useQuestion, type ReviewAnswer } from './queries'

export interface SegmentActions {
  /** Saves a line's text and/or struck-out mark; true when saved. */
  saveLine: (lineId: string, change: { text?: string; struck?: boolean }) => Promise<boolean>
  merge: (first: string, second: string) => Promise<unknown>
  split: (segmentId: string, regionId: string) => Promise<unknown>
  reassign: (segmentId: string, label: string | null) => Promise<unknown>
  moveBoundary: (upper: string, lower: string, regionId: string) => Promise<unknown>
}

const FLAG: Record<string, { label: string; tone: Tone }> = {
  low_ocr: { label: 'Low OCR confidence', tone: 'amber' },
  off_target: { label: 'May be off target', tone: 'amber' },
  blank: { label: 'Blank', tone: 'gray' },
  mark_manually: { label: 'Mark manually', tone: 'purple' },
  duplicate: { label: 'Answered twice', tone: 'amber' },
  before_first_answer: { label: 'Before the first answer', tone: 'gray' },
  label_disagrees: { label: 'Label and wording disagree', tone: 'amber' },
  number_unread: { label: 'Question number unread', tone: 'amber' },
}

function LineEditor({
  line,
  index,
  view,
  prev,
  next,
  actions,
  busy,
  onClose,
}: {
  line: LineView
  index: number
  view: SegmentView
  prev: SegmentView | null
  next: SegmentView | null
  actions: SegmentActions
  busy: boolean
  onClose: () => void
}) {
  const [text, setText] = useState(line.text)
  const changed = text.trim() !== line.text.trim() && text.trim() !== ''
  const first = index === 0
  const upper = view.segment.id
  const regionOf = line.id
  return (
    <div className="mt-2 space-y-3 rounded-lg border border-cyan-500/40 bg-gray-950 p-3">
      {isTextLine(line) ? (
        <>
          <label className="block text-xs font-semibold text-gray-200">
            Correct the text of this line
            <textarea
              rows={2}
              className={`${compactInputClass} mt-1 resize-y font-mono`}
              value={text}
              maxLength={2000}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <p className="text-[10px] text-gray-400">
            {line.readBy.length > 0 && `Read by ${line.readBy.join(', ')}. `}
            Your correction is saved with the booklet for the OCR accuracy benchmark.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <SmallButton
              icon="fa-solid fa-check"
              tone="purple"
              disabled={busy || !changed}
              onClick={() =>
                void actions.saveLine(line.id, { text: text.trim() }).then((ok) => ok && onClose())
              }
            >
              Save text
            </SmallButton>
            <label className="flex items-center gap-1.5 text-xs text-gray-200">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-purple-500"
                checked={line.struck}
                disabled={busy}
                onChange={(e) => void actions.saveLine(line.id, { struck: e.target.checked })}
              />
              The student struck this line out (leave it out of scoring)
            </label>
          </div>
        </>
      ) : (
        <p className="text-xs text-gray-300">A drawing: correct its nodes and edges below.</p>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-gray-800 pt-2">
        <SmallButton
          icon="fa-solid fa-scissors"
          tone="gray"
          disabled={busy || first}
          onClick={() => void actions.split(upper, regionOf)}
        >
          Split here
        </SmallButton>
        <SmallButton
          icon="fa-solid fa-arrow-down-long"
          tone="gray"
          disabled={busy || first || next === null}
          onClick={() => next && void actions.moveBoundary(upper, next.segment.id, regionOf)}
        >
          Move this line and the rest to the next segment
        </SmallButton>
        <SmallButton
          icon="fa-solid fa-arrow-up-long"
          tone="gray"
          disabled={busy || first || prev === null}
          onClick={() => prev && void actions.moveBoundary(prev.segment.id, upper, regionOf)}
        >
          Move the lines before this one to the previous segment
        </SmallButton>
        <SmallButton tone="gray" onClick={onClose}>
          Close
        </SmallButton>
      </div>
    </div>
  )
}

/**
 * "Question 1 Answer Clip": one segment's text as one paragraph (as in the prototype) and the
 * segment tools. Each line of the paragraph opens its editor; lines the OCR was unsure of are
 * highlighted without a number (teachers do not need the confidence figures).
 */
export function SegmentCard({
  view,
  prev,
  next,
  answer,
  labels,
  selected,
  onSelect,
  canEdit,
  busy,
  editingLine,
  onEditLine,
  actions,
  questionId,
}: {
  view: SegmentView
  prev: SegmentView | null
  next: SegmentView | null
  answer: ReviewAnswer | undefined
  labels: string[]
  selected: boolean
  onSelect: () => void
  canEdit: boolean
  busy: boolean
  editingLine: string | null
  onEditLine: (lineId: string | null) => void
  actions: SegmentActions
  /** The bank question this segment's slot is marked against (null: unassigned or unlinked). */
  questionId: string | null
}) {
  const heading = view.label === null ? 'Unassigned text' : `Question ${view.label} Answer Clip`
  const flags = [...new Set([...(answer?.suggestion?.flags ?? []), ...view.segment.flags])].filter(
    (f) => f in FLAG,
  )
  const border = view.label === null ? 'border-amber-500/40' : 'border-emerald-500/40'
  const mark = answer?.suggestion?.mark
  const question = useQuestion(view.label === null ? null : questionId)
  const official = question.data?.text ?? null
  // the student's copy of the question, folded under the official wording (D158)
  const echo = official ? questionEcho(view.lines, official) : 0
  const [showCopy, setShowCopy] = useState(false)
  // a line being edited is never hidden
  const copyOpen = showCopy || view.lines.slice(0, echo).some((l) => l.id === editingLine)

  const renderLine = (line: LineView, k: number, first: boolean) => {
    const open = editingLine === line.id
    const text = isTextLine(line) ? line.text || '(nothing read)' : '[drawing]'
    const unsure = line.flagged && !line.struck
    const tone = line.struck
      ? 'text-gray-500 line-through'
      : unsure
        ? 'rounded-sm bg-amber-500/20 text-amber-100 underline decoration-amber-400 decoration-dotted'
        : ''
    const ring = open ? 'rounded-sm bg-cyan-500/20 outline outline-cyan-400' : ''
    const name = unsure ? `Line ${k + 1}, low OCR confidence: ${text}` : `Line ${k + 1}: ${text}`
    const toggle = () => {
      onSelect()
      onEditLine(open ? null : line.id)
    }
    return (
      <span key={line.id}>
        {!first && ' '}
        {/* A span, not a button: a button cannot wrap across lines of a paragraph. A click
            shows the line on the page; it also opens the editor when editable. */}
        <span
          role="button"
          tabIndex={0}
          aria-expanded={canEdit ? open : undefined}
          aria-pressed={canEdit ? undefined : open}
          aria-label={name}
          title={
            unsure ? 'Hard to read: check this line against the page' : 'Show this line on the page'
          }
          onClick={toggle}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              toggle()
            }
          }}
          className={`cursor-pointer hover:bg-cyan-500/10 focus-visible:outline focus-visible:outline-cyan-400 ${tone} ${ring}`}
        >
          {text}
        </span>
      </span>
    )
  }

  return (
    <article
      aria-label={`Segment ${view.segment.position + 1}: ${heading}`}
      className={`rounded-xl border bg-gray-900/90 p-4 shadow-lg ${border} ${selected ? 'ring-2 ring-cyan-400' : ''}`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={onSelect}
          className={`text-xs font-bold tracking-wider uppercase ${view.label === null ? 'text-amber-400' : 'text-emerald-400'}`}
        >
          {heading}
        </button>
        <div className="flex flex-wrap items-center gap-1.5">
          {view.pages.length > 0 && (
            <Badge tone="gray">
              {view.pages.length === 1
                ? `Page ${view.pages[0]}`
                : `Pages ${view.pages[0]}–${view.pages[view.pages.length - 1]}`}
            </Badge>
          )}
          {answer && !answer.attempted ? (
            <Badge tone="gray">Not attempted</Badge>
          ) : (
            answer && (
              <Badge tone={answer.rescore_pending ? 'cyan' : 'indigo'}>
                {answer.rescore_pending
                  ? 'Re-scoring…'
                  : mark === null || mark === undefined
                    ? 'No AI mark'
                    : `Suggested ${mark} / ${answer.max_marks}`}
              </Badge>
            )
          )}
        </div>
      </div>
      {(flags.length > 0 || view.lowLines > 0) && (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {flags.map((f) => (
            <li key={f}>
              <Badge tone={FLAG[f]?.tone ?? 'gray'}>{FLAG[f]?.label ?? f}</Badge>
            </li>
          ))}
          {view.lowLines > 0 && (
            <li>
              <Badge tone="amber">
                {view.lowLines} {view.lowLines === 1 ? 'line' : 'lines'} to check
              </Badge>
            </li>
          )}
        </ul>
      )}

      {official && (
        <section
          aria-label={`Question ${view.label ?? ''}`}
          className="mb-2 rounded-lg border border-purple-500/30 bg-purple-950/20 p-3 text-xs text-gray-200"
        >
          <h4 className="mb-1 text-[10px] font-bold tracking-wider text-purple-300 uppercase">
            Question {view.label}
          </h4>
          <p>{official}</p>
          {echo > 0 && (
            <>
              <button
                type="button"
                aria-expanded={copyOpen}
                onClick={() => setShowCopy((v) => !v)}
                className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-400 hover:text-white"
              >
                <i
                  className={`fa-solid ${copyOpen ? 'fa-chevron-down' : 'fa-chevron-right'} text-[9px]`}
                  aria-hidden="true"
                />
                The student&apos;s copy of the question ({echo} {echo === 1 ? 'line' : 'lines'})
              </button>
              {copyOpen && (
                <p
                  aria-label={`Student's copy of question ${view.label ?? ''}`}
                  className="mt-1 rounded-md bg-black/40 p-2 font-mono leading-relaxed break-words text-gray-400"
                >
                  {view.lines.slice(0, echo).map((line, k) => renderLine(line, k, k === 0))}
                </p>
              )}
            </>
          )}
        </section>
      )}

      {view.lines.length === 0 ? (
        <p className="text-xs text-gray-400">No text was found in this segment.</p>
      ) : (
        <>
          {official && (
            <h4 className="mb-1 text-[10px] font-bold tracking-wider text-gray-400 uppercase">
              Answer
            </h4>
          )}
          <p
            aria-label={`Text of ${heading}`}
            className="rounded-lg border border-gray-800 bg-black/50 p-3 font-mono text-xs leading-relaxed break-words text-cyan-200"
          >
            {view.lines.slice(echo).map((line, k) => renderLine(line, k + echo, k === 0))}
          </p>
        </>
      )}
      {canEdit &&
        view.lines.map((line, k) =>
          editingLine === line.id ? (
            <LineEditor
              key={`${line.id}:${line.text}`}
              line={line}
              index={k}
              view={view}
              prev={prev}
              next={next}
              actions={actions}
              busy={busy}
              onClose={() => onEditLine(null)}
            />
          ) : null,
        )}

      {canEdit && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-800 pt-3">
          <label className="flex items-center gap-1.5 text-xs text-gray-300">
            {view.label === null ? 'Assign to' : 'Reassign to'}
            <select
              aria-label={`Reassign ${heading}`}
              className="rounded-lg border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-white"
              value={view.label ?? ''}
              disabled={busy}
              onChange={(e) =>
                void actions.reassign(
                  view.segment.id,
                  e.target.value === '' ? null : e.target.value,
                )
              }
            >
              <option value="">Unassigned tray</option>
              {labels.map((l) => (
                <option key={l} value={l}>
                  Question {l}
                </option>
              ))}
            </select>
          </label>
          <SmallButton
            icon="fa-solid fa-object-group"
            tone="gray"
            disabled={busy || next === null}
            onClick={() => next && void actions.merge(view.segment.id, next.segment.id)}
          >
            Merge with next segment
          </SmallButton>
        </div>
      )}
    </article>
  )
}
