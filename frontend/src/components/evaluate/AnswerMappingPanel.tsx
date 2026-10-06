import type { DiagramGraph, Rect } from '../../lib/graph'
import { formatMark, type Comparison, type SlotInfo } from '../../lib/review'
import { isTextLine, percent, type SegmentView } from '../../lib/segments'
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

function StudentText({ view }: { view: SegmentView }) {
  const lines = view.lines.filter(isTextLine)
  const struck = lines.filter((l) => l.struck).length
  const shown = lines.filter((l) => !l.struck)
  return (
    <div>
      {shown.length === 0 ? (
        <p className="text-gray-500 italic">No text was read for this answer.</p>
      ) : (
        <p className="leading-relaxed whitespace-pre-wrap">
          {shown.map((l, k) => (
            <span key={l.id}>
              {k > 0 && '\n'}
              {l.flagged ? (
                <span
                  className="rounded-sm bg-amber-500/15 underline decoration-amber-400 decoration-dotted"
                  title="Low OCR confidence: check this line against the page"
                >
                  {l.text}
                </span>
              ) : (
                l.text
              )}
            </span>
          ))}
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
      className="space-y-3 p-4 lg:col-span-4"
    >
      <h3 className="text-xs font-bold tracking-wider text-purple-400 uppercase">
        Panel A: Answer Mapping
      </h3>

      <div className="rounded-xl bg-gray-900 p-3 text-xs text-gray-200">
        <p className="mb-1 flex flex-wrap items-center gap-2 font-semibold text-white">
          Question {label}
          <Badge tone="gray">{formatMark(answer.max_marks)} marks</Badge>
          {question && <span className="font-mono text-[10px] text-gray-400">{question.code}</span>}
        </p>
        {question ? (
          <p>{question.text}</p>
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

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        <section
          aria-label="Reference key"
          className="rounded-xl bg-gray-900 p-3 text-xs text-gray-200"
        >
          <h4 className="mb-1 text-[10px] font-bold tracking-wider text-gray-400 uppercase">
            Reference key
          </h4>
          {question && question.reference_answers.length > 0 ? (
            <ul className="space-y-2">
              {question.reference_answers.map((r) => (
                <li key={r.id}>
                  {r.guidance_only && (
                    <Badge tone="amber" className="mb-1">
                      Guidance only
                    </Badge>
                  )}{' '}
                  {r.synthetic && (
                    <Badge tone="gray" className="mb-1">
                      Synthetic key
                    </Badge>
                  )}
                  <p>“{r.text}”</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-400">
              {question ? 'This question has no reference answer.' : 'No key to show.'}
            </p>
          )}
        </section>

        <section
          aria-label="Student's answer"
          className="rounded-xl bg-gray-900 p-3 font-mono text-xs text-cyan-100"
        >
          <h4 className="mb-1 font-sans text-[10px] font-bold tracking-wider text-gray-400 uppercase">
            Student&apos;s answer (OCR)
          </h4>
          {views.length === 0 && (
            <p className="text-gray-500 italic">Nothing was written for this question.</p>
          )}
          {views.map((v, k) => (
            <div key={v.segment.id} className={k > 0 ? 'mt-3 border-t border-gray-800 pt-2' : ''}>
              {views.length > 1 && (
                <p className="mb-1 font-sans text-[10px] text-amber-300">Copy {k + 1}</p>
              )}
              <StudentText view={v} />
            </div>
          ))}
        </section>
      </div>

      {views.length > 0 && (
        <p className="text-[11px] text-gray-400">
          <strong className="text-gray-300">Question match:</strong>{' '}
          {views
            .map((v) => {
              const how = SOURCE[v.segment.source]
              const score = v.segment.match_score
              return `placed ${how}${score !== null ? `, match ${percent(score)}` : ''}`
            })
            .join('; ')}
          .
        </p>
      )}

      {total > 0 && (
        <section aria-label="Keywords and items matched" className="space-y-1.5">
          <h4 className="text-[10px] font-bold tracking-wider text-gray-400 uppercase">
            Keywords &amp; items matched: {matched.length} of {total}
          </h4>
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
