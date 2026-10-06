import { useState } from 'react'
import {
  DEFAULT_TAGS,
  MAX_TAGS,
  MAX_TAG_LENGTH,
  cleanTag,
  formatMark,
  loadCustomTags,
  markProblem,
  saveCustomTags,
  stageOf,
} from '../../lib/review'
import { SmallButton, TextArea, compactInputClass } from '../schema/controls'
import { Badge, GlassPanel } from '../ui'
import type { ReviewAnswer } from './queries'

export interface Decision {
  /** Null: accept the AI's mark. */
  teacherMark: number | null
  tags: string[]
  remarks: string
}

interface Props {
  answer: ReviewAnswer
  /** The booklet is approved: reopening an answer opens an amendment. */
  bookletApproved: boolean
  /** Why nothing can be changed now (another teacher's lock…); undefined when it can. */
  readOnlyReason: string | undefined
  busy: boolean
  onApprove: (decision: Decision) => void
  onSkip: () => void
  onReopen: (reason: string) => void
  onWithdraw: () => void
}

function TagPicker({
  selected,
  onChange,
  disabled,
}: {
  selected: string[]
  onChange: (tags: string[]) => void
  disabled: boolean
}) {
  const [custom, setCustom] = useState(loadCustomTags)
  const [draft, setDraft] = useState('')
  const all = [
    ...DEFAULT_TAGS,
    ...custom.filter((t) => !(DEFAULT_TAGS as readonly string[]).includes(t)),
  ]
  const full = selected.length >= MAX_TAGS

  const toggle = (tag: string) =>
    onChange(selected.includes(tag) ? selected.filter((t) => t !== tag) : [...selected, tag])

  const add = () => {
    const tag = cleanTag(draft)
    if (!tag) return
    const known = all.find((t) => t.toLowerCase() === tag.toLowerCase())
    if (!known) {
      const next = [...custom, tag]
      setCustom(next)
      saveCustomTags(next)
    }
    const use = known ?? tag
    if (!selected.includes(use) && !full) onChange([...selected, use])
    setDraft('')
  }

  const remove = (tag: string) => {
    const next = custom.filter((t) => t !== tag)
    setCustom(next)
    saveCustomTags(next)
    onChange(selected.filter((t) => t !== tag))
  }

  return (
    <fieldset disabled={disabled} className="space-y-2">
      <legend className="mb-1 text-[10px] font-bold tracking-wider text-gray-400 uppercase">
        Feedback tags ({selected.length}/{MAX_TAGS})
      </legend>
      <ul className="flex flex-wrap gap-1.5">
        {all.map((tag) => {
          const on = selected.includes(tag)
          const mine = custom.includes(tag)
          return (
            <li key={tag} className="flex items-center">
              <button
                type="button"
                aria-pressed={on}
                disabled={!on && full}
                onClick={() => toggle(tag)}
                className={`border px-2 py-0.5 text-[11px] transition disabled:opacity-40 ${mine ? 'rounded-l-full border-r-0' : 'rounded-full'} ${
                  on
                    ? 'border-pink-400/60 bg-pink-950 text-pink-200'
                    : 'border-gray-700 bg-gray-900 text-gray-300 hover:border-gray-500'
                }`}
              >
                {tag}
              </button>
              {mine && (
                <button
                  type="button"
                  aria-label={`Remove the tag ${tag} from my list`}
                  title="Remove from my list"
                  onClick={() => remove(tag)}
                  className={`rounded-r-full border px-1.5 py-0.5 text-[10px] text-gray-400 hover:text-white ${
                    on ? 'border-pink-400/60 bg-pink-950' : 'border-gray-700 bg-gray-900'
                  }`}
                >
                  <i className="fa-solid fa-xmark" aria-hidden="true" />
                </button>
              )}
            </li>
          )
        })}
      </ul>
      <div className="flex gap-2">
        <input
          aria-label="New feedback tag"
          className={compactInputClass}
          value={draft}
          maxLength={MAX_TAG_LENGTH}
          placeholder="Your own tag"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <SmallButton
          icon="fa-solid fa-plus"
          tone="gray"
          disabled={cleanTag(draft) === ''}
          onClick={add}
        >
          Add tag
        </SmallButton>
      </div>
    </fieldset>
  )
}

function Approved({
  answer,
  bookletApproved,
  readOnlyReason,
  busy,
  onReopen,
}: Pick<Props, 'answer' | 'bookletApproved' | 'readOnlyReason' | 'busy' | 'onReopen'>) {
  const [asking, setAsking] = useState(false)
  const [reason, setReason] = useState('')
  const a = answer.approval
  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/30 p-3 text-xs">
        <p className="flex items-center gap-2 font-semibold text-emerald-300">
          <i className="fa-solid fa-circle-check" aria-hidden="true" />
          Approved: {a ? `${formatMark(a.teacher_mark)} / ${formatMark(answer.max_marks)}` : ''}
          {a?.overridden && <Badge tone="amber">Overridden</Badge>}
        </p>
        {a && (
          <dl className="mt-2 space-y-1 text-gray-300">
            <div className="flex gap-2">
              <dt className="text-gray-400">AI suggested</dt>
              <dd>{a.ai_mark === null ? 'no AI mark' : formatMark(a.ai_mark)}</dd>
            </div>
            {a.tags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {a.tags.map((t) => (
                  <Badge key={t} tone="gray">
                    {t}
                  </Badge>
                ))}
              </div>
            )}
            {a.remarks && <p className="whitespace-pre-wrap text-gray-200">“{a.remarks}”</p>}
          </dl>
        )}
      </div>
      {asking ? (
        <div className="space-y-2 rounded-xl border border-amber-500/30 bg-amber-950/30 p-3">
          <p className="text-xs text-amber-100">
            {bookletApproved
              ? 'The booklet is approved. Reopening opens an amendment: the result sheet you issued stays valid, and approving this answer again issues the next version.'
              : 'Reopening takes your approval back so you can decide this answer again.'}
          </p>
          <TextArea
            label="Reason (optional)"
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex gap-2">
            <SmallButton
              tone="purple"
              icon="fa-solid fa-lock-open"
              disabled={busy}
              onClick={() => onReopen(reason)}
            >
              {bookletApproved ? 'Open amendment' : 'Reopen'}
            </SmallButton>
            <SmallButton tone="gray" onClick={() => setAsking(false)}>
              Cancel
            </SmallButton>
          </div>
        </div>
      ) : (
        <SmallButton
          icon="fa-solid fa-lock-open"
          tone="gray"
          disabled={busy || readOnlyReason !== undefined}
          onClick={() => setAsking(true)}
        >
          {bookletApproved ? 'Reopen to amend' : 'Reopen answer'}
        </SmallButton>
      )}
    </div>
  )
}

function Editor({
  answer,
  readOnlyReason,
  busy,
  onApprove,
  onSkip,
  onWithdraw,
}: Pick<Props, 'answer' | 'readOnlyReason' | 'busy' | 'onApprove' | 'onSkip' | 'onWithdraw'>) {
  const s = answer.suggestion
  const aiMark = s?.mark ?? null
  const step = s?.mark_step ?? 0.5
  const earlier = answer.draft ? answer.approval : null
  const mustMark = aiMark === null

  const [override, setOverride] = useState(mustMark)
  const [markText, setMarkText] = useState(aiMark === null ? '' : formatMark(aiMark))
  const [tags, setTags] = useState<string[]>(earlier ? earlier.tags : [])
  const [remarks, setRemarks] = useState(earlier ? earlier.remarks : '')

  const forced = mustMark || override
  const problem = forced ? markProblem(markText, step, answer.max_marks) : null
  const locked = readOnlyReason !== undefined
  const waiting = answer.rescore_pending
  const stage = stageOf(answer)

  const toggle = () => {
    if (mustMark) return
    if (!override) setMarkText(aiMark === null ? '' : formatMark(aiMark))
    setOverride(!override)
  }

  return (
    <div className="space-y-4">
      {answer.draft && (
        <p className="rounded-xl border border-amber-500/30 bg-amber-950/30 px-3 py-2 text-xs text-amber-100">
          <Badge tone="amber" className="mr-1.5">
            Amendment draft
          </Badge>
          {answer.draft.reason ? `Reason: ${answer.draft.reason}` : 'No reason was given.'}
        </p>
      )}
      {stage === 'skipped' && (
        <p className="text-[11px] text-gray-400">
          <Badge tone="gray" className="mr-1.5">
            Skipped
          </Badge>
          You left this one for later.
        </p>
      )}

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <span id="override-label" className="text-xs font-semibold text-gray-200">
            Override AI Score
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={forced}
            aria-labelledby="override-label"
            disabled={locked || mustMark}
            onClick={toggle}
            className={`relative h-5 w-9 rounded-full transition disabled:opacity-60 ${forced ? 'bg-pink-600' : 'bg-gray-700'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform ${forced ? 'translate-x-4' : ''}`}
            />
          </button>
        </div>
        {mustMark && (
          <p className="text-[11px] text-amber-300">
            The AI gave no mark for this answer: enter yours.
          </p>
        )}
        <label className="block text-[11px] text-gray-400">
          Teacher marks (0 to {formatMark(answer.max_marks)}, in steps of {formatMark(step)})
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={answer.max_marks}
            step={step}
            disabled={locked || !forced}
            value={forced ? markText : aiMark === null ? '' : formatMark(aiMark)}
            onChange={(e) => setMarkText(e.target.value)}
            aria-invalid={problem !== null}
            aria-describedby={problem ? 'mark-problem' : undefined}
            className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900 p-2 text-center text-sm font-bold text-white focus:border-pink-500 focus:outline-none disabled:opacity-60"
          />
        </label>
        {problem && (
          <p id="mark-problem" role="alert" className="text-[11px] text-red-300">
            {problem}
          </p>
        )}
      </div>

      <TagPicker selected={tags} onChange={setTags} disabled={locked} />

      <TextArea
        label="Remarks for the student"
        rows={3}
        maxLength={2000}
        disabled={locked}
        value={remarks}
        onChange={(e) => setRemarks(e.target.value)}
      />

      <div className="space-y-2">
        <button
          type="button"
          disabled={locked || busy || waiting || problem !== null}
          onClick={() =>
            onApprove({
              teacherMark: forced ? Number(markText) : null,
              tags,
              remarks,
            })
          }
          className="w-full rounded-xl bg-pink-600 py-2 text-xs font-bold text-white shadow-lg transition hover:bg-pink-500 disabled:opacity-50"
        >
          <i className="fa-solid fa-check mr-1.5" aria-hidden="true" />
          {answer.draft ? 'Approve amendment' : 'Approve answer'}
        </button>
        <div className="flex flex-wrap gap-2">
          {!answer.draft && (
            <SmallButton
              icon="fa-solid fa-forward"
              tone="gray"
              disabled={locked || busy}
              onClick={onSkip}
            >
              Skip for now
            </SmallButton>
          )}
          {answer.draft && (
            <SmallButton
              icon="fa-solid fa-rotate-left"
              tone="gray"
              disabled={locked || busy}
              onClick={onWithdraw}
            >
              Withdraw amendment
            </SmallButton>
          )}
        </div>
        {waiting && (
          <p className="text-[11px] text-cyan-200">
            Waiting for the new suggestion before you can approve.
          </p>
        )}
      </div>
    </div>
  )
}

/** Panel C, "Teacher Final Grading": the teacher's decision on one answer. The AI suggests, the
 * teacher decides: nothing is final until the answer is approved. */
export function GradingPanel(props: Props) {
  const { answer, readOnlyReason } = props
  const approved = stageOf(answer) === 'approved'
  return (
    <GlassPanel
      as="section"
      aria-label="Panel C: Teacher Final Grading"
      className="space-y-3 p-4 lg:col-span-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-bold tracking-wider text-pink-400 uppercase">
          Panel C: Teacher Final Grading
        </h3>
        <Badge tone="purple">
          <i className="fa-solid fa-user-check" aria-hidden="true" />
          Human In The Loop
        </Badge>
      </div>
      {readOnlyReason && (
        <p role="note" className="text-[11px] text-amber-200">
          <i className="fa-solid fa-eye mr-1.5 text-amber-300" aria-hidden="true" />
          {readOnlyReason}
        </p>
      )}
      {approved ? (
        <Approved
          answer={answer}
          bookletApproved={props.bookletApproved}
          readOnlyReason={readOnlyReason}
          busy={props.busy}
          onReopen={props.onReopen}
        />
      ) : (
        <Editor
          answer={answer}
          readOnlyReason={readOnlyReason}
          busy={props.busy}
          onApprove={props.onApprove}
          onSkip={props.onSkip}
          onWithdraw={props.onWithdraw}
        />
      )}
    </GlassPanel>
  )
}
