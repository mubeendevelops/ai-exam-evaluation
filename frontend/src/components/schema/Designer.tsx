import { useMemo, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { useBlueprintCheck, type Check, type CheckState } from '../../hooks/useBlueprintCheck'
import {
  calculatedTotal,
  fileNameOf,
  fillSection,
  newSection,
  nextNumber,
  nextSectionLabel,
  questionCount,
  renumber,
  toDocument,
  type BlueprintDocument,
  type FormSection,
  type FormState,
} from '../../lib/blueprint'
import { GlassPanel, useToast } from '../ui'
import { SmallButton } from './controls'
import { HeaderForm } from './HeaderForm'
import { JsonPanel } from './JsonPanel'
import { DesignerKind, DesignerLinks } from './kind'
import { SectionCard } from './SectionCard'

export interface Verdict {
  document: BlueprintDocument
  json: string
  state: CheckState
  check: Check | undefined
}

/** The form as a document and the server's verdict on it (D58: the rules live there). */
export function useVerdict(form: FormState): Verdict {
  const document = useMemo(() => toDocument(form), [form])
  const json = useMemo(() => JSON.stringify(document, null, 2), [document])
  const { state, check } = useBlueprintCheck(document)
  return { document, json, state, check }
}

interface Props {
  form: FormState
  setForm: Dispatch<SetStateAction<FormState>>
  verdict: Verdict
  /** Buttons in the blueprint panel's heading (Save, Generate…). */
  actions?: ReactNode
  /** The old designer's Reset Form. */
  onReset?: () => void
  batches?: string[]
  /** A paper's descriptors in use, for suggestions. */
  descriptors?: string[]
  /** Booklets pin a version of this paper: links are read-only (D222). */
  locked?: boolean
}

/**
 * The blueprint designer (P6, a component since P33): header, sections, live totals and the
 * generated JSON with the server's verdict. The form's kind sets the mode: a schema (D219) has
 * no exam code and no question links; a paper has both. Saving is the page's business.
 */
export function Designer({
  form,
  setForm,
  verdict,
  actions,
  onReset,
  batches,
  descriptors,
  locked = false,
}: Props) {
  const toast = useToast()
  const schema = form.kind === 'schema'
  const links = useMemo(() => ({ subjectId: form.subjectId, locked }), [form.subjectId, locked])
  const patch = (change: Partial<FormState>) => setForm((f) => ({ ...f, ...change }))
  const changeSection = (key: string, next: FormSection) =>
    setForm((f) => ({ ...f, sections: f.sections.map((s) => (s.key === key ? next : s)) }))

  function addSection() {
    setForm((f) => ({
      ...f,
      sections: [...f.sections, newSection(nextSectionLabel(f), nextNumber(f), 5, '2')],
    }))
  }

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(verdict.json)
      toast.show('JSON copied to the clipboard.')
    } catch {
      toast.show('Could not copy. Select the JSON and copy it by hand.', 'red')
    }
  }

  function downloadJson() {
    const url = URL.createObjectURL(new Blob([verdict.json], { type: 'application/json' }))
    const link = globalThis.document.createElement('a')
    link.href = url
    link.download = fileNameOf(form)
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <DesignerKind.Provider value={form.kind}>
      <DesignerLinks.Provider value={links}>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <GlassPanel
            as="section"
            aria-labelledby="blueprint-title"
            className="space-y-6 p-6 lg:col-span-7"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800 pb-3">
              <h2
                id="blueprint-title"
                className="flex items-center gap-2 text-sm font-bold tracking-wider text-purple-400 uppercase"
              >
                <i
                  className={schema ? 'fa-solid fa-table-cells-large' : 'fa-solid fa-file-pen'}
                  aria-hidden="true"
                />{' '}
                {schema ? 'Schema Blueprint' : 'Exam Blueprint'}
              </h2>
              {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
            </div>

            <HeaderForm form={form} onChange={patch} batches={batches} descriptors={descriptors} />

            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800 pb-2">
                <h3 className="flex items-center gap-1.5 text-xs font-bold tracking-wider text-cyan-400 uppercase">
                  <i className="fa-solid fa-list-ol" aria-hidden="true" /> Section Breakdown
                  Architecture
                </h3>
                <div className="flex gap-2">
                  <SmallButton
                    icon="fa-solid fa-arrow-down-1-9"
                    tone="gray"
                    onClick={() => setForm((f) => renumber(f))}
                  >
                    Renumber 1…n
                  </SmallButton>
                  <SmallButton icon="fa-solid fa-plus" onClick={addSection}>
                    Add Section
                  </SmallButton>
                </div>
              </div>

              {form.sections.map((section, index) => (
                <SectionCard
                  key={section.key}
                  section={section}
                  index={index}
                  nextNumber={nextNumber(form)}
                  onChange={(next) => changeSection(section.key, next)}
                  onRemove={() =>
                    setForm((f) => ({
                      ...f,
                      sections: f.sections.filter((s) => s.key !== section.key),
                    }))
                  }
                  onFill={(count, marks) =>
                    setForm((f) => fillSection(f, section.key, count, marks))
                  }
                />
              ))}
              {form.sections.length === 0 && (
                <p className="text-xs text-amber-400">
                  A blueprint needs at least one section. Use Add Section.
                </p>
              )}

              <div className="flex items-center justify-between rounded-xl border border-purple-500/30 bg-gray-900/90 p-4">
                <div>
                  <span className="block text-xs font-medium text-gray-400">
                    Calculated Question Count
                  </span>
                  <span className="text-xl font-bold text-purple-400">
                    {questionCount(form)} Questions
                  </span>
                </div>
                <div className="text-right">
                  <span className="block text-xs font-medium text-gray-400">Total Paper Marks</span>
                  <span className="text-2xl font-extrabold text-emerald-400">
                    {calculatedTotal(form)} Marks
                  </span>
                </div>
              </div>
            </div>
          </GlassPanel>

          <div className="lg:col-span-5">
            <div className="lg:sticky lg:top-24">
              <JsonPanel
                json={verdict.json}
                state={verdict.state}
                check={verdict.check}
                kind={form.kind}
                onCopy={() => void copyJson()}
                onDownload={downloadJson}
                onReset={onReset}
              />
            </div>
          </div>
        </div>
      </DesignerLinks.Provider>
    </DesignerKind.Provider>
  )
}
