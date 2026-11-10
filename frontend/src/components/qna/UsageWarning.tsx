import { Modal } from '../ui'
import { SmallButton } from '../schema/controls'
import type { QuestionUsage } from './usage'

/**
 * Before a question that papers link is saved (D223): which papers and open units the new
 * version reaches, and what happens to answers in them.
 */
export function UsageWarning({
  code,
  usage,
  busy,
  marks,
  onConfirm,
  onCancel,
}: {
  code: string
  usage: QuestionUsage
  busy: boolean
  /** Set when the marks are being changed: the slots that link the question carry the old ones. */
  marks?: { from: number; to: number }
  onConfirm: () => void
  onCancel: () => void
}) {
  const papers = usage.papers
  return (
    <Modal
      open
      onClose={onCancel}
      size="max-w-xl"
      eyebrow="Used in question papers"
      title={`${code} is used in ${papers.length} ${papers.length === 1 ? 'paper' : 'papers'}`}
      description="Saving makes a new version of the question; every paper that links it uses that version from now on."
      footer={
        <div className="flex justify-end gap-2">
          <SmallButton tone="gray" onClick={onCancel}>
            Cancel
          </SmallButton>
          <SmallButton
            icon="fa-solid fa-floppy-disk"
            tone="purple"
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? 'Saving…' : 'Save the question'}
          </SmallButton>
        </div>
      }
    >
      <div className="space-y-3 text-xs text-gray-300">
        <ul aria-label="Papers that link this question" className="space-y-2">
          {papers.map((p) => (
            <li key={p.id} className="rounded-lg border border-gray-800 bg-gray-950/60 p-2.5">
              <p className="font-semibold text-white">
                {p.title}
                {p.exam_code && <span className="ml-2 font-mono text-cyan-300">{p.exam_code}</span>}
              </p>
              <p className="text-[11px] text-gray-400">
                {p.open_units.length === 0
                  ? 'No open unit uses it now.'
                  : `Open ${p.open_units.length === 1 ? 'unit' : 'units'}: ${p.open_units
                      .map((u) => `${u.code} (${u.year})`)
                      .join(', ')}`}
              </p>
            </li>
          ))}
        </ul>
        {marks && (
          <p
            role="note"
            className="rounded-lg border border-red-500/40 bg-red-950/40 p-2.5 text-red-200"
          >
            You are changing the marks from {marks.from} to {marks.to}. The slots that link this
            question carry {marks.from}, and a paper cannot be saved with a link whose marks differ:
            change those slots, or link another question, in each paper above.
          </p>
        )}
        <p
          role="note"
          className="rounded-lg border border-amber-500/40 bg-amber-950/40 p-2.5 text-amber-200"
        >
          Answers that are not approved yet are scored again with the new version when their booklet
          is next opened. Approved answers and frozen records keep the version they were marked
          with.
        </p>
      </div>
    </Modal>
  )
}
