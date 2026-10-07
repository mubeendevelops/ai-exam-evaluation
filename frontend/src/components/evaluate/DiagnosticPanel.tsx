import { formatMark } from '../../lib/review'
import { percent } from '../../lib/segments'
import { Badge, GlassPanel } from '../ui'
import type { Question, ReviewAnswer } from './queries'

export const FLAG_TEXT: Record<string, { label: string; detail: string }> = {
  low_ocr: {
    label: 'Low OCR confidence',
    detail: 'Some lines were hard to read. Check them against the page before trusting the mark.',
  },
  off_target: {
    label: 'Possibly off target',
    detail: 'The text does not look like an answer to this question, or names contradict the key.',
  },
  blank: { label: 'Blank answer', detail: 'Little or no text was found: 0 marks are suggested.' },
  mark_manually: {
    label: 'Mark manually',
    detail: 'The key is guidance only, so the AI gives no mark. Enter yours.',
  },
  duplicate: {
    label: 'Answered twice',
    detail: 'Both copies were scored; the higher suggestion is shown.',
  },
  scorer_disagreement: {
    label: 'The two AI scorers disagree',
    detail:
      'The LLM and the rubric scorer differ by more than one band on at least one criterion. Both are shown below: look at those yourself.',
  },
}

type Criterion = NonNullable<ReviewAnswer['suggestion']>['criteria'][number]

/** The LLM's second opinion beside the rubric scorer's credit (P19): shown only when the college
 * has the LLM scorer on and it answered. The mark above never uses it. */
function SecondOpinion({ c, name }: { c: Criterion; name: string }) {
  const o = c.second_opinion
  if (!o) return null
  const disagrees = o.disagrees
  return (
    <div
      role="group"
      aria-label={`${name}: LLM second opinion`}
      className={`space-y-1 rounded-lg border p-2 text-[11px] ${
        disagrees ? 'border-amber-500/50 bg-amber-950/30' : 'border-gray-700 bg-gray-950/40'
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-semibold text-violet-300">LLM second opinion</span>
        <span className="font-mono text-violet-300">
          {formatMark(o.marks)} / {formatMark(c.weight)}
        </span>
      </div>
      <Bar credit={o.credit} label={`${name}: LLM ${Math.round(o.credit * 100)}% credit`} />
      <p className="text-gray-400">
        Credit {Math.round(o.credit * 100)}% · {o.scorer}
      </p>
      <p className="text-gray-200">{o.reason}</p>
      {disagrees && (
        <p className="text-amber-300">
          <i className="fa-solid fa-code-compare mr-1" aria-hidden="true" />
          The two scorers differ by more than one band ({Math.round(c.credit * 100)}% against{' '}
          {Math.round(o.credit * 100)}%): look at this one yourself. The suggested mark uses the
          rubric scorer&apos;s credit.
        </p>
      )}
    </div>
  )
}

function Bar({ credit, label }: { credit: number; label: string }) {
  const value = Math.round(credit * 100)
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      className="h-1.5 overflow-hidden rounded-full bg-gray-800"
    >
      <div
        className={`h-full rounded-full ${credit >= 0.75 ? 'bg-emerald-500' : credit > 0 ? 'bg-amber-500' : 'bg-red-500'}`}
        style={{ width: `${value}%` }}
      />
    </div>
  )
}

/** Panel B, "AI Diagnostic Reasoning": the suggested mark ("Auto-Suggested"), how sure the AI is
 * and the credit and reason of every rubric criterion. */
export function DiagnosticPanel({
  answer,
  question,
  ocrConfidence,
}: {
  answer: ReviewAnswer
  question: Question | undefined
  ocrConfidence: number | null
}) {
  const s = answer.suggestion
  const label = (id: string, k: number) =>
    question?.rubric.criteria.find((c) => c.criterion.id === id)?.criterion.label ??
    `Criterion ${k + 1}`

  return (
    <GlassPanel
      as="section"
      aria-label="Panel B: AI Diagnostic Reasoning"
      className="space-y-3 p-4 lg:col-span-4"
    >
      <h3 className="text-xs font-bold tracking-wider text-cyan-400 uppercase">
        Panel B: AI Diagnostic Reasoning
      </h3>

      <div className="rounded-xl border border-cyan-500/30 bg-cyan-950/30 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-bold text-cyan-300">
            {answer.rescore_pending
              ? 'Suggested AI Marks: updating…'
              : s && s.mark !== null
                ? `Suggested AI Marks: ${formatMark(s.mark)} / ${formatMark(answer.max_marks)}`
                : 'No AI mark'}
          </span>
          <Badge tone="cyan">Auto-Suggested</Badge>
        </div>
        {answer.rescore_pending && (
          <p role="status" className="mt-1 text-[11px] text-cyan-200">
            <i className="fa-solid fa-spinner fa-spin mr-1" aria-hidden="true" />A new suggestion is
            on its way after a change. You can approve once it arrives.
          </p>
        )}
        <dl className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-gray-300">
          <div>
            <dt className="text-gray-400">AI confidence: relevance</dt>
            <dd className="font-semibold text-white">{percent(s?.relevance ?? null)}</dd>
          </div>
          <div>
            <dt className="text-gray-400">AI confidence: handwriting read</dt>
            <dd className="font-semibold text-white">{percent(ocrConfidence)}</dd>
          </div>
        </dl>
      </div>

      {s && s.flags.length > 0 && (
        <ul aria-label="Flags" className="space-y-1.5">
          {s.flags.map((f) => (
            <li
              key={f}
              className="rounded-lg border border-amber-500/30 bg-amber-950/30 px-3 py-1.5 text-xs text-amber-100"
            >
              <i className="fa-solid fa-flag mr-1.5 text-amber-300" aria-hidden="true" />
              <strong>{FLAG_TEXT[f]?.label ?? f}.</strong> {FLAG_TEXT[f]?.detail}
            </li>
          ))}
        </ul>
      )}

      {s && s.criteria.length > 0 && (
        <ul aria-label="Rubric criteria" className="space-y-2">
          {s.criteria.map((c, k) => {
            const name = label(c.criterion_id, k)
            return (
              <li
                key={c.criterion_id}
                className="space-y-1.5 rounded-xl border border-gray-800 bg-gray-900/70 p-3 text-xs"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold text-white">{name}</span>
                  <span className="font-mono text-cyan-300">
                    {formatMark(c.marks)} / {formatMark(c.weight)}
                  </span>
                </div>
                <Bar credit={c.credit} label={`${name}: ${Math.round(c.credit * 100)}% credit`} />
                <p className="text-[11px] text-gray-400">
                  Credit {Math.round(c.credit * 100)}%
                  {c.similarity !== null && `, similarity ${percent(c.similarity)}`} · {c.scorer}
                </p>
                {c.reason && <p className="text-gray-200">{c.reason}</p>}
                {c.flags.includes('check') && (
                  <p className="text-[11px] text-amber-300">
                    <i className="fa-solid fa-triangle-exclamation mr-1" aria-hidden="true" />
                    Borderline: look at this one yourself.
                  </p>
                )}
                {c.flags.includes('manual') && (
                  <p className="text-[11px] text-amber-300">Marked by you: no AI credit.</p>
                )}
                <SecondOpinion c={c} name={name} />
              </li>
            )
          })}
        </ul>
      )}

      {s && s.reasons.length > 0 && (
        <ul aria-label="Notes" className="list-disc space-y-1 pl-4 text-[11px] text-gray-300">
          {s.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}

      {!s && <p className="text-xs text-gray-400">This answer has not been scored yet.</p>}
    </GlassPanel>
  )
}
