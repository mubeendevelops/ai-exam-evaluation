import { useState } from 'react'
import { formatMark, progressOf, stageOf, type Stage } from '../../lib/review'
import { DownloadSheetButton, sheetFileName } from '../evaluated/DownloadSheetButton'
import { SmallButton, TextArea } from '../schema/controls'
import { Badge, GlassPanel, type Tone } from '../ui'
import type { Review, ReviewAnswer } from './queries'

const STAGE: Record<Stage, { label: string; tone: Tone }> = {
  todo: { label: 'To approve', tone: 'cyan' },
  skipped: { label: 'Skipped', tone: 'gray' },
  approved: { label: 'Approved', tone: 'emerald' },
  draft: { label: 'Amendment draft', tone: 'amber' },
  none: { label: 'Not attempted', tone: 'gray' },
}

const OUTCOME: Record<string, string> = {
  counted: 'Counted',
  'not attempted': 'Not attempted',
  'not counted: best N': 'Not counted: best N',
  'not counted: other OR alternative scored higher':
    'Not counted: the other OR alternative scored higher',
}

function when(iso: string): string {
  return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

function markOf(a: ReviewAnswer | undefined): string {
  if (!a) return '—'
  if (a.approval && !a.draft) return formatMark(a.approval.teacher_mark)
  if (a.suggestion?.mark != null) return `${formatMark(a.suggestion.mark)} (AI)`
  return '—'
}

/**
 * The booklet summary: every question with its mark, which marks count (best N, OR) and where
 * each answer stands; approval of the booklet, the result sheet versions, and reopening an answer
 * to amend it.
 */
export function BookletSummary({
  review,
  canEdit,
  readOnlyReason,
  busy,
  onOpenAnswer,
  onApproveBooklet,
  onReopen,
  onWithdraw,
}: {
  review: Review
  canEdit: boolean
  readOnlyReason: string | undefined
  busy: boolean
  onOpenAnswer: (label: string) => void
  onApproveBooklet: () => void
  onReopen: (answer: ReviewAnswer, reason: string) => void
  onWithdraw: (answer: ReviewAnswer) => void
}) {
  const [reopening, setReopening] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const answers = new Map(review.answers.map((a) => [a.slot_label, a]))
  const progress = progressOf(review.answers)
  const latest = review.sheets[review.sheets.length - 1]
  const open = progress.attempted - progress.approved

  return (
    <div className="space-y-4">
      {review.amendment_in_progress && (
        <p
          role="status"
          className="rounded-xl border border-amber-500/30 bg-amber-950/30 px-4 py-2 text-xs text-amber-100"
        >
          <Badge tone="amber" className="mr-2">
            Amendment in progress
          </Badge>
          {progress.drafts} {progress.drafts === 1 ? 'answer is' : 'answers are'} being amended.
          Result sheet v{(latest?.version ?? 0) + 1} is issued when the last one is approved; v
          {latest?.version ?? 1} stays valid until then.
        </p>
      )}

      <GlassPanel as="section" aria-label="Booklet summary" className="p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-white">
            <i className="fa-solid fa-table-list mr-2 text-cyan-400" aria-hidden="true" />
            Booklet summary
          </h3>
          <p className="text-xs text-gray-300">
            Total{' '}
            <strong className="text-base text-white">
              {formatMark(review.totals.total)} / {formatMark(review.totals.max_marks)}
            </strong>
            {!review.approved && (
              <span className="ml-2 text-gray-400">(provisional until approved)</span>
            )}
          </p>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">
              Every question with its mark and whether it counts
            </caption>
            <thead>
              <tr className="border-b border-gray-800 text-[10px] tracking-wider text-gray-400 uppercase">
                <th scope="col" className="py-2 pr-3">
                  Section
                </th>
                <th scope="col" className="py-2 pr-3">
                  Question
                </th>
                <th scope="col" className="py-2 pr-3">
                  Status
                </th>
                <th scope="col" className="py-2 pr-3">
                  Mark
                </th>
                <th scope="col" className="py-2 pr-3">
                  Counts
                </th>
                <th scope="col" className="py-2">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {review.totals.slots.map((slot) => {
                const a = answers.get(slot.slot_label)
                const stage = a ? stageOf(a) : 'none'
                const meta = STAGE[stage]
                const notCounted = !slot.counted && slot.outcome !== 'not attempted'
                return (
                  <tr
                    key={`${slot.section_label}-${slot.slot_label}`}
                    className={`border-b border-gray-800/70 ${notCounted ? 'text-gray-500' : 'text-gray-200'}`}
                  >
                    <td className="py-2 pr-3">{slot.section_label}</td>
                    <td className="py-2 pr-3 font-semibold">Q{slot.slot_label}</td>
                    <td className="py-2 pr-3">
                      <Badge tone={meta.tone}>{meta.label}</Badge>
                    </td>
                    <td className={`py-2 pr-3 font-mono ${notCounted ? 'line-through' : ''}`}>
                      {markOf(a)}
                      {a && ` / ${formatMark(a.max_marks)}`}
                      {a?.approval?.overridden && !a.draft && (
                        <span className="ml-1 font-sans text-[10px] text-amber-300 no-underline">
                          overridden
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {notCounted ? (
                        <Badge tone="gray">{OUTCOME[slot.outcome] ?? slot.outcome}</Badge>
                      ) : (
                        <span className="text-gray-400">
                          {OUTCOME[slot.outcome] ?? slot.outcome}
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {a && stage !== 'none' && (
                        <span className="inline-flex flex-wrap justify-end gap-1.5">
                          <SmallButton
                            tone="gray"
                            icon="fa-solid fa-eye"
                            onClick={() => onOpenAnswer(slot.slot_label)}
                          >
                            Open <span className="sr-only">question {slot.slot_label}</span>
                          </SmallButton>
                          {stage === 'approved' && (
                            <SmallButton
                              tone="purple"
                              icon="fa-solid fa-lock-open"
                              disabled={!canEdit || busy}
                              onClick={() => {
                                setReopening(a.id)
                                setReason('')
                              }}
                            >
                              {review.approved ? 'Reopen to amend' : 'Reopen'}{' '}
                              <span className="sr-only">question {slot.slot_label}</span>
                            </SmallButton>
                          )}
                          {stage === 'draft' && (
                            <SmallButton
                              tone="gray"
                              icon="fa-solid fa-rotate-left"
                              disabled={!canEdit || busy}
                              onClick={() => onWithdraw(a)}
                            >
                              Withdraw{' '}
                              <span className="sr-only">
                                amendment of question {slot.slot_label}
                              </span>
                            </SmallButton>
                          )}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[10px] text-gray-400">
          Marks marked “AI” are suggestions the teacher has not approved yet. Struck-through marks
          do not count towards the total.
        </p>

        {reopening && (
          <div
            role="group"
            aria-label="Reopen an answer"
            className="mt-3 space-y-2 rounded-xl border border-amber-500/30 bg-amber-950/30 p-3"
          >
            <p className="text-xs text-amber-100">
              {review.approved
                ? 'This opens an amendment. The issued result sheet stays valid; approving the answer again issues the next version.'
                : 'This takes your approval back so you can decide the answer again.'}
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
                onClick={() => {
                  const target = review.answers.find((x) => x.id === reopening)
                  setReopening(null)
                  if (target) onReopen(target, reason)
                }}
              >
                {review.approved ? 'Open amendment' : 'Reopen answer'}
              </SmallButton>
              <SmallButton tone="gray" onClick={() => setReopening(null)}>
                Cancel
              </SmallButton>
            </div>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-gray-800 pt-4">
          <p className="text-xs text-gray-300">
            {review.approved ? (
              <>
                <i
                  className="fa-solid fa-circle-check mr-1.5 text-emerald-400"
                  aria-hidden="true"
                />
                Booklet approved
                {latest && `: result sheet v${latest.version} is the current one.`}
              </>
            ) : open > 0 ? (
              `${open} of ${progress.attempted} answers still need your approval.`
            ) : (
              'Every answer is approved. The booklet can be approved now.'
            )}
          </p>
          {!review.approved && (
            <button
              type="button"
              onClick={onApproveBooklet}
              disabled={!review.can_approve || !canEdit || busy}
              className="rounded-xl bg-gradient-to-r from-emerald-600 to-cyan-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg transition disabled:opacity-50"
            >
              <i className="fa-solid fa-stamp mr-2" aria-hidden="true" />
              Approve booklet
            </button>
          )}
        </div>
        {readOnlyReason && !review.approved && (
          <p role="note" className="mt-2 text-[11px] text-amber-200">
            {readOnlyReason}
          </p>
        )}
      </GlassPanel>

      {review.sheets.length > 0 && (
        <GlassPanel as="section" aria-label="Result sheets" className="p-4">
          <h3 className="mb-3 text-sm font-semibold text-white">
            <i className="fa-solid fa-file-lines mr-2 text-purple-400" aria-hidden="true" />
            Result sheets
          </h3>
          <ul className="space-y-2 text-xs">
            {[...review.sheets].reverse().map((sheet) => (
              <li
                key={sheet.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-gray-800 bg-gray-900/70 px-3 py-2"
              >
                <span className="flex items-center gap-2 font-semibold text-white">
                  Result sheet v{sheet.version}
                  {sheet.version === latest?.version && <Badge tone="emerald">Current</Badge>}
                </span>
                <span className="text-gray-300">
                  {formatMark(sheet.total)} / {formatMark(sheet.max_marks)}
                </span>
                <span className="text-[11px] text-gray-400">
                  {when(sheet.issued_at)}
                  {sheet.note && ` · ${sheet.note}`}
                </span>
                {sheet.pdf_url ? (
                  <DownloadSheetButton
                    url={sheet.pdf_url}
                    filename={sheetFileName(undefined, sheet.version)}
                    label={`Download PDF v${sheet.version}`}
                    tone={sheet.version === latest?.version ? 'purple' : 'gray'}
                  />
                ) : (
                  <span className="text-[11px] text-gray-500">No PDF stored</span>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[10px] text-gray-400">
            Earlier versions stay valid and keep their own PDF. All versions are listed under the
            Evaluated tab.
          </p>
        </GlassPanel>
      )}
    </div>
  )
}
