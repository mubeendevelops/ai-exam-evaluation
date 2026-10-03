import type { Check, CheckState } from '../../hooks/useBlueprintCheck'
import { Badge, GlassPanel, type Tone } from '../ui'
import { SmallButton } from './controls'

interface Props {
  json: string
  state: CheckState
  check: Check | undefined
  onCopy: () => void
  onDownload: () => void
  onReset: () => void
}

const STATUS: Record<CheckState, { tone: Tone; icon: string; text: (c?: Check) => string }> = {
  checking: { tone: 'gray', icon: 'fa-solid fa-spinner fa-spin', text: () => 'Checking…' },
  valid: { tone: 'emerald', icon: 'fa-solid fa-circle-check', text: () => 'Valid' },
  invalid: {
    tone: 'red',
    icon: 'fa-solid fa-circle-xmark',
    text: (c) => {
      const n = c?.issues.length ?? 0
      return `Invalid · ${n} ${n === 1 ? 'problem' : 'problems'}`
    },
  },
  unavailable: {
    tone: 'amber',
    icon: 'fa-solid fa-triangle-exclamation',
    text: () => 'Cannot check right now',
  },
}

/** The right column: the generated schema, its verdict, and Copy / Download / Reset. */
export function JsonPanel({ json, state, check, onCopy, onDownload, onReset }: Props) {
  const status = STATUS[state]
  return (
    <GlassPanel as="section" aria-labelledby="schema-json-title" className="space-y-4 p-6">
      <div className="flex items-center justify-between border-b border-gray-800 pb-2">
        <h2
          id="schema-json-title"
          className="flex items-center gap-1.5 text-xs font-bold tracking-wider text-cyan-400 uppercase"
        >
          <i className="fa-solid fa-code" aria-hidden="true" /> Generated Question Schema (JSON)
        </h2>
        <span role="status" aria-label="Schema status">
          <Badge tone={status.tone}>
            <i className={status.icon} aria-hidden="true" /> {status.text(check)}
          </Badge>
        </span>
      </div>

      {state === 'unavailable' && (
        <p className="text-xs text-amber-300">
          The server could not be reached, so the rules were not checked. The JSON below is still
          what your form describes.
        </p>
      )}

      {check && state !== 'unavailable' && (
        <div className="space-y-2 text-xs">
          {state === 'valid' && (
            <p className="text-emerald-300">
              {check.computed_total} marks · {check.question_count} questions ·{' '}
              {check.sections.length} {check.sections.length === 1 ? 'section' : 'sections'}.
            </p>
          )}
          {check.issues.length > 0 && (
            <ul aria-label="Problems" className="space-y-1.5">
              {check.issues.map((issue, i) => (
                <li
                  key={`${issue.path}-${i}`}
                  className="rounded-lg border border-red-500/30 bg-red-950/30 px-3 py-2"
                >
                  <span className="block font-mono text-[10px] text-red-300">{issue.path}</span>
                  <span className="text-red-100">{issue.message}</span>
                </li>
              ))}
            </ul>
          )}
          {check.warnings.length > 0 && (
            <ul aria-label="Warnings" className="space-y-1.5">
              {check.warnings.map((warning, i) => (
                <li
                  key={`${warning.path}-${i}`}
                  className="rounded-lg border border-amber-500/30 bg-amber-950/30 px-3 py-2 text-amber-100"
                >
                  {warning.message}
                </li>
              ))}
            </ul>
          )}
          {state === 'valid' && check.unlinked.length > 0 && (
            <p className="text-gray-400">
              {check.unlinked.length} of the answerable parts are not linked to a question yet
              (allowed here; link them in the question bank before registering booklets).
            </p>
          )}
        </div>
      )}

      <pre
        aria-label="Schema JSON"
        data-testid="schema-json"
        tabIndex={0}
        className="max-h-[480px] overflow-auto rounded-xl border border-gray-800 bg-gray-950 p-4 font-mono text-xs text-emerald-400"
      >
        {json}
      </pre>

      <div className="flex flex-wrap gap-2">
        <SmallButton icon="fa-regular fa-copy" onClick={onCopy}>
          Copy JSON
        </SmallButton>
        <SmallButton icon="fa-solid fa-download" onClick={onDownload}>
          Download .json
        </SmallButton>
        <SmallButton icon="fa-solid fa-rotate-left" tone="gray" onClick={onReset}>
          Reset Form
        </SmallButton>
      </div>
    </GlassPanel>
  )
}
