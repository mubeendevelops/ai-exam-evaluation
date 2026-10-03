import { useEffect, useMemo, useState } from 'react'
import { PageBanner } from '../components/app/PageBanner'
import { SmallButton } from '../components/schema/controls'
import { HeaderForm } from '../components/schema/HeaderForm'
import { JsonPanel } from '../components/schema/JsonPanel'
import { SectionCard } from '../components/schema/SectionCard'
import { GlassPanel, Modal, useToast } from '../components/ui'
import { useBlueprintCheck } from '../hooks/useBlueprintCheck'
import {
  calculatedTotal,
  fileNameOf,
  fillSection,
  initialForm,
  newSection,
  nextNumber,
  nextSectionLabel,
  questionCount,
  renumber,
  toDocument,
  type FormSection,
  type FormState,
} from '../lib/blueprint'
import { clearDraft, loadDraft, saveDraft } from '../lib/draft'

/**
 * `/schema`: the exam blueprint designer. Every input rebuilds the JSON on the right at once;
 * the server then says whether it is a valid blueprint (`useBlueprintCheck`).
 */
export default function SchemaPage() {
  const toast = useToast()
  const [form, setForm] = useState<FormState>(() => loadDraft() ?? initialForm())
  const [confirmReset, setConfirmReset] = useState(false)

  const document = useMemo(() => toDocument(form), [form])
  const json = useMemo(() => JSON.stringify(document, null, 2), [document])
  const { state, check } = useBlueprintCheck(document)

  useEffect(() => saveDraft(form), [form])

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
      await navigator.clipboard.writeText(json)
      toast.show('JSON copied to the clipboard.')
    } catch {
      toast.show('Could not copy. Select the JSON and copy it by hand.', 'red')
    }
  }

  function downloadJson() {
    const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }))
    const link = globalThis.document.createElement('a')
    link.href = url
    link.download = fileNameOf(form)
    link.click()
    URL.revokeObjectURL(url)
  }

  function reset() {
    clearDraft()
    setForm(initialForm())
    setConfirmReset(false)
    toast.show('The form was reset.')
  }

  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-file-signature"
        iconTone="text-purple-400"
        title="Question Paper Studio & Schema Engine"
        description="Design mark blueprints (e.g. 50-mark, 100-mark) with choice rules, OR pairs and sub-parts; the schema is generated as you type."
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <GlassPanel
          as="section"
          aria-labelledby="blueprint-title"
          className="space-y-6 p-6 lg:col-span-7"
        >
          <div className="flex items-center justify-between border-b border-gray-800 pb-3">
            <h2
              id="blueprint-title"
              className="flex items-center gap-2 text-sm font-bold tracking-wider text-purple-400 uppercase"
            >
              <i className="fa-solid fa-file-pen" aria-hidden="true" /> Exam Blueprint
            </h2>
          </div>

          <HeaderForm form={form} onChange={patch} />

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
                onFill={(count, marks) => setForm((f) => fillSection(f, section.key, count, marks))}
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
              json={json}
              state={state}
              check={check}
              onCopy={() => void copyJson()}
              onDownload={downloadJson}
              onReset={() => setConfirmReset(true)}
            />
          </div>
        </div>
      </div>

      <Modal
        open={confirmReset}
        onClose={() => setConfirmReset(false)}
        title="Reset the form?"
        description="Everything you entered here is cleared. This cannot be undone."
        variant="app"
        footer={
          <div className="flex justify-end gap-2">
            <SmallButton tone="gray" onClick={() => setConfirmReset(false)}>
              Cancel
            </SmallButton>
            <SmallButton tone="purple" onClick={reset}>
              Yes, reset
            </SmallButton>
          </div>
        }
      >
        <p className="text-xs text-gray-300">
          Nothing is saved on the server by this page, so a reset starts a fresh blueprint.
        </p>
      </Modal>
    </div>
  )
}
