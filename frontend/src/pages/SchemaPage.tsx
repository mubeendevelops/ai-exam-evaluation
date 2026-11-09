import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { PageBanner } from '../components/app/PageBanner'
import { SmallButton } from '../components/schema/controls'
import { Designer } from '../components/schema/Designer'
import { SavedPapers } from '../components/schema/SavedPapers'
import { useBlueprintEditor, useSavedBlueprint, type Blueprint } from '../components/schema/saved'
import { Modal, useToast } from '../components/ui'
import { initialForm } from '../lib/blueprint'
import { clearDraft, loadDraft, saveDraft } from '../lib/draft'

/**
 * `/schema`: the older Schema Designer, kept in "Older screens" until P34 builds the papers
 * pages (D215). Since P33 it loads a saved blueprint (`?blueprint=<id>`, closing O20) and saves
 * (Save = the next version; Save as new = another blueprint). A form not saved yet is kept in
 * session storage; a loaded one is the server's. Each address gets a fresh editor.
 */
export default function SchemaPage() {
  const [params] = useSearchParams()
  const id = params.get('blueprint') ?? ''
  const saved = useSavedBlueprint(id)

  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-file-signature"
        iconTone="text-purple-400"
        title="Question Paper Studio & Schema Engine"
        description="Design mark blueprints (e.g. 50-mark, 100-mark) with choice rules, OR pairs and sub-parts; the schema is generated as you type."
      />
      {!id && <Workbench key="draft" saved={null} />}
      {id && saved.isError && (
        <p role="alert" className="text-xs text-red-300">
          That blueprint was not found: it is not in your subjects, or it was deleted.
        </p>
      )}
      {id && saved.isPending && <p className="text-xs text-gray-400">Loading the blueprint…</p>}
      {id && saved.data && <Workbench key={id} saved={saved.data} />}
      <SavedPapers openId={id} />
    </div>
  )
}

function Workbench({ saved }: { saved: Blueprint | null }) {
  const toast = useToast()
  const [, setParams] = useSearchParams()
  const editor = useBlueprintEditor(saved, () => loadDraft() ?? initialForm())
  const { form, setForm, verdict, dirty, version, busy } = editor
  const [confirmReset, setConfirmReset] = useState(false)

  useEffect(() => {
    if (!saved) saveDraft(form)
  }, [form, saved])

  async function save(asNew: boolean) {
    const data = await editor.save(asNew)
    if (data && data.id !== saved?.id) {
      if (!saved) clearDraft()
      setParams({ blueprint: data.id }) // opens the new blueprint
    }
  }

  function reset() {
    clearDraft()
    setForm(initialForm())
    setConfirmReset(false)
    toast.show('The form was reset.')
  }

  const ready = verdict.state === 'valid' && !busy
  const actions = (
    <>
      {saved && (
        <span className="self-center text-[11px] text-gray-400">
          Version {version}
          {dirty ? ' · unsaved changes' : ' · saved'}
        </span>
      )}
      <SmallButton
        icon="fa-solid fa-floppy-disk"
        tone="purple"
        disabled={!ready || !dirty}
        onClick={() => void save(false)}
      >
        {saved ? 'Save' : 'Save blueprint'}
      </SmallButton>
      {saved && (
        <>
          <SmallButton
            icon="fa-regular fa-copy"
            tone="gray"
            disabled={!ready}
            onClick={() => void save(true)}
          >
            Save as new
          </SmallButton>
          <SmallButton
            icon="fa-solid fa-file-circle-plus"
            tone="gray"
            onClick={() => setParams({})}
          >
            New blueprint
          </SmallButton>
        </>
      )}
    </>
  )

  return (
    <>
      <Designer
        form={form}
        setForm={setForm}
        verdict={verdict}
        actions={actions}
        onReset={saved ? undefined : () => setConfirmReset(true)}
      />
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
          The form has not been saved, so a reset starts a fresh blueprint.
        </p>
      </Modal>
    </>
  )
}
