import { SmallButton } from '../schema/controls'
import { GlassPanel } from '../ui'
import type { RescoreEntry } from './useEdits'

/** "Re-score results": what each edit did to the suggested marks, as the new suggestions arrive. */
export function RescorePanel({
  entries,
  onDismiss,
}: {
  entries: RescoreEntry[]
  onDismiss: (answerId: string) => void
}) {
  if (entries.length === 0) return null
  return (
    <GlassPanel as="section" aria-label="Re-score results" className="space-y-2 p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
        <i className="fa-solid fa-rotate text-cyan-400" aria-hidden="true" />
        Re-score results
      </h3>
      <p className="text-[11px] text-gray-400">
        Only the answers your edit touched are scored again. These are suggestions: you decide every
        mark in the next step.
      </p>
      <ul className="space-y-1.5" aria-live="polite">
        {entries.map((e) => {
          const was = e.before === null ? 'no mark' : `${e.before}`
          const now = e.after === null ? 'no AI mark' : `${e.after}`
          const changed = e.before !== e.after
          return (
            <li
              key={e.answerId}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-800 bg-gray-900/70 px-3 py-2 text-xs"
            >
              <span className="text-gray-200">
                <strong className="text-white">Question {e.label}</strong>{' '}
                {e.pending ? (
                  <span className="text-cyan-300">
                    <i className="fa-solid fa-spinner fa-spin mr-1" aria-hidden="true" />
                    Re-scoring… (suggested {was} / {e.max} until then)
                  </span>
                ) : changed ? (
                  <span>
                    new suggestion{' '}
                    <strong className="text-emerald-300">
                      {now} / {e.max}
                    </strong>{' '}
                    <span className="text-gray-400">(was {was})</span>
                  </span>
                ) : (
                  <span>
                    suggestion unchanged at{' '}
                    <strong className="text-white">
                      {now} / {e.max}
                    </strong>
                  </span>
                )}
              </span>
              {!e.pending && (
                <SmallButton tone="gray" onClick={() => onDismiss(e.answerId)}>
                  Dismiss
                </SmallButton>
              )}
            </li>
          )
        })}
      </ul>
    </GlassPanel>
  )
}
