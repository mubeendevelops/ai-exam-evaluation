import { useState } from 'react'
import type { DiagramGraph, Rect } from '../../lib/graph'
import { questionEcho } from '../../lib/questionEcho'
import { formatMark, type Comparison, type SlotInfo } from '../../lib/review'
import { isTextLine, type LineView, type SegmentView } from '../../lib/segments'
import { Badge, GlassPanel } from '../ui'
import { DiagramOverlay } from './DiagramOverlay'
import type { Question, ReviewAnswer } from './queries'

export interface OverlayInput {
  key: string
  comparison: Comparison
  drawing?: {
    graph: DiagramGraph
    imagePath: string
    imageSize: { width: number; height: number }
    crop: Rect
  }
}

const SOURCE: Record<SegmentView['segment']['source'], string> = {
  rule: 'by the question number written in the booklet',
  similarity: 'by how closely the text matches the question',
  teacher: 'by you',
}

function distinct(lists: string[][]): string[] {
  return [...new Set(lists.flat())]
}

function Words({ lines }: { lines: LineView[] }) {
  return lines.map((l, k) => (
    <span key={l.id}>
      {k > 0 && ' '}
      {l.flagged ? (
        <span
          className="rounded-sm bg-amber-500/15 underline decoration-amber-400 decoration-dotted"
          title="Hard to read: check this line against the page"
        >
          {l.text}
        </span>
      ) : (
        l.text
      )}
    </span>
  ))
}

/** The student's text as a paragraph; their copy of the question (D158) is folded away, the
 * official wording being at the top of the panel. */
function StudentText({ view, question }: { view: SegmentView; question: string | null }) {
  const [showCopy, setShowCopy] = useState(false)
  const echo = question ? questionEcho(view.lines, question) : 0
  const copy = view.lines.slice(0, echo)
  const lines = view.lines.slice(echo).filter(isTextLine)
  const struck = lines.filter((l) => l.struck).length
  const shown = lines.filter((l) => !l.struck)
  return (
    <div>
      {echo > 0 && (
        <div className="mb-2">
          <button
            type="button"
            aria-expanded={showCopy}
            onClick={() => setShowCopy((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-full border border-gray-700 px-2 py-0.5 text-[10px] text-gray-400 hover:border-gray-500 hover:text-white"
          >
            <i
              className={`fa-solid ${showCopy ? 'fa-chevron-down' : 'fa-chevron-right'} text-[9px]`}
              aria-hidden="true"
            />
            The student&apos;s copy of the question ({echo} {echo === 1 ? 'line' : 'lines'})
          </button>
          {showCopy && (
            <p className="mt-1.5 rounded-md bg-black/30 p-2 leading-relaxed break-words text-gray-400 italic">
              <Words lines={copy} />
            </p>
          )}
        </div>
      )}
      {shown.length === 0 ? (
        <p className="text-gray-500 italic">No text was read for this answer.</p>
      ) : (
        <p className="leading-relaxed break-words">
          <Words lines={shown} />
        </p>
      )}
      {struck > 0 && (
        <p className="mt-1 text-[10px] text-gray-400">
          {struck} struck-out {struck === 1 ? 'line is' : 'lines are'} left out of the scoring.
        </p>
      )}
    </div>
  )
}

const HEADING = 'text-[10px] font-bold tracking-wider text-gray-400 uppercase'

/** Keys longer than this start folded to a few lines. */
const LONG_KEY = 280

function KeyBadges({ r }: { r: { guidance_only: boolean; synthetic: boolean } }) {
  return (
    <>
      {r.guidance_only && <Badge tone="amber">Guidance only</Badge>}
      {r.synthetic && <Badge tone="gray">Synthetic key</Badge>}
    </>
  )
}

/** The reference key under the student's text, folded when long so the answer stays in view. */
function ReferenceKey({ question }: { question: Question | undefined }) {
  const [open, setOpen] = useState(false)
  const keys = question?.reference_answers ?? []
  const long = keys.reduce((n, r) => n + r.text.length, 0) > LONG_KEY
  const folded = long && !open
  return (
    <section
      aria-label="Reference key"
      className="rounded-xl border-l-2 border-purple-500/50 bg-gray-900/70 p-3 text-xs text-gray-300"
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <h4 className={HEADING}>Reference key</h4>
        {keys.length === 1 && keys[0] && <KeyBadges r={keys[0]} />}
      </div>
      {keys.length > 0 ? (
        <>
          <div className={`relative ${folded ? 'max-h-24 overflow-hidden' : ''}`}>
            <ul className="space-y-2 leading-relaxed">
              {keys.map((r) => (
                <li key={r.id}>
                  {keys.length > 1 && (
                    <span className="mr-1.5 inline-flex gap-1 align-middle">
                      <KeyBadges r={r} />
                    </span>
                  )}
                  {r.text}
                </li>
              ))}
            </ul>
            {folded && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-gray-900 to-transparent" />
            )}
          </div>
          {long && (
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="mt-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-purple-300 hover:text-purple-200"
            >
              {open ? 'Show less' : 'Show full key'}
              <i
                className={`fa-solid ${open ? 'fa-chevron-up' : 'fa-chevron-down'} text-[9px]`}
                aria-hidden="true"
              />
            </button>
          )}
        </>
      ) : (
        <p className="text-gray-400">
          {question ? 'This question has no reference answer.' : 'No key to show.'}
        </p>
      )}
    </section>
  )
}

/** Panel A, "Answer Mapping": the question, the reference key beside the student's text, how the
 * text was matched to the question, the keywords and items found, and the diagram check. */
export function AnswerMappingPanel({
  label,
  slot,
  question,
  questionLoading,
  questionFailed,
  views,
  answer,
  overlays,
}: {
  label: string
  slot: SlotInfo | undefined
  question: Question | undefined
  questionLoading: boolean
  questionFailed: boolean
  views: SegmentView[]
  answer: ReviewAnswer
  overlays: OverlayInput[]
}) {
  const criteria = answer.suggestion?.criteria ?? []
  const matched = distinct(criteria.map((c) => c.matched))
  const missing = distinct(criteria.map((c) => c.missing)).filter((m) => !matched.includes(m))
  const total = matched.length + missing.length

  return (
    <GlassPanel
      as="section"
      aria-label="Panel A: Answer Mapping"
      className="space-y-4 p-4 lg:col-span-4"
    >
      <h3 className="text-xs font-bold tracking-wider text-purple-400 uppercase">
        Panel A: Answer Mapping
      </h3>

      <div className="rounded-xl border border-purple-500/20 bg-purple-950/20 p-3 text-xs text-gray-200">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <p className="font-semibold text-white">Question {label}</p>
          {question && <span className="font-mono text-[10px] text-gray-400">{question.code}</span>}
          <Badge tone="gray" className="ml-auto">
            {formatMark(answer.max_marks)} marks
          </Badge>
        </div>
        {question ? (
          <p className="leading-relaxed">{question.text}</p>
        ) : questionLoading ? (
          <p className="text-gray-400">Loading the question…</p>
        ) : questionFailed ? (
          <p role="alert" className="text-red-300">
            The question could not be loaded.
          </p>
        ) : (
          <p className="text-gray-400">
            {slot && slot.questionId === null
              ? 'No question of the bank is linked to this place on the paper.'
              : 'The question text is not available.'}
          </p>
        )}
      </div>

      {total > 0 && (
        <section aria-label="Keywords and items matched" className="space-y-2">
          <div className="flex items-center gap-2">
            <h4 className={HEADING}>Keywords &amp; items</h4>
            <span className="ml-auto text-[10px] text-gray-400">
              {matched.length} of {total} found
            </span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-gray-800" aria-hidden="true">
            <div
              className="h-full rounded-full bg-emerald-500"
              style={{ width: `${(matched.length / total) * 100}%` }}
            />
          </div>
          <ul className="flex flex-wrap gap-1.5">
            {matched.map((m) => (
              <li key={`m-${m}`}>
                <Badge tone="emerald">
                  <i className="fa-solid fa-check" aria-hidden="true" />
                  <span className="sr-only">Found: </span>
                  {m}
                </Badge>
              </li>
            ))}
            {missing.map((m) => (
              <li key={`x-${m}`}>
                <Badge tone="red">
                  <i className="fa-solid fa-xmark" aria-hidden="true" />
                  <span className="sr-only">Missing: </span>
                  {m}
                </Badge>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section
        aria-label="Student's answer"
        className="rounded-xl border-l-2 border-cyan-500/60 bg-gray-900/70 p-3 text-[13px] text-gray-100"
      >
        <h4 className={`mb-2 ${HEADING}`}>Student&apos;s answer</h4>
        {views.length === 0 && (
          <p className="text-gray-500 italic">Nothing was written for this question.</p>
        )}
        {views.map((v, k) => (
          <div key={v.segment.id} className={k > 0 ? 'mt-3 border-t border-gray-800 pt-3' : ''}>
            {views.length > 1 && (
              <p className="mb-1 text-[10px] font-semibold text-amber-300">Copy {k + 1}</p>
            )}
            <StudentText view={v} question={question?.text ?? null} />
          </div>
        ))}
        {views.length > 0 && (
          <p className="mt-3 flex items-start gap-1.5 border-t border-gray-800 pt-2 text-[10px] text-gray-400">
            <i className="fa-solid fa-link mt-0.5 text-[9px]" aria-hidden="true" />
            <span>
              <strong className="font-semibold text-gray-300">Question match:</strong>{' '}
              {views.map((v) => `placed ${SOURCE[v.segment.source]}`).join('; ')}.
            </span>
          </p>
        )}
      </section>

      <ReferenceKey question={question} />

      {overlays.map((o, k) => (
        <DiagramOverlay
          key={o.key}
          title={overlays.length > 1 ? `Diagram ${k + 1} check` : 'Diagram check'}
          comparison={o.comparison}
          drawing={o.drawing}
        />
      ))}
    </GlassPanel>
  )
}
