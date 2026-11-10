import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { SmallButton } from '../../components/schema/controls'
import { Designer } from '../../components/schema/Designer'
import {
  useBlueprintEditor,
  useSavedBlueprint,
  type Blueprint,
} from '../../components/schema/saved'
import { PaperPreview } from '../../components/sme/PaperPreview'
import { useDescriptors } from '../../components/sme/papers'
import { EmptyState } from '../../components/ui'
import { initialForm } from '../../lib/blueprint'

const link =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700'

/**
 * One question paper in the designer (`/sme/papers/:blueprintId`, P34): paper mode (exam code,
 * descriptor, each slot linked to a question through the picker). Save stores the next version;
 * Preview shows the saved version's PDF. Once booklets pin a version, only the title,
 * descriptor, description, target batch, duration and course code may change (D222).
 */
export default function PaperEditorPage() {
  const { blueprintId = '' } = useParams()
  const saved = useSavedBlueprint(blueprintId)

  if (saved.isError) {
    return (
      <EmptyState
        icon="fa-solid fa-file-lines"
        title="Paper not found"
        action={
          <Link to="/sme/papers" className={link}>
            Back to Manage Question Papers
          </Link>
        }
      >
        It is not in your subjects, or it has been deleted.
      </EmptyState>
    )
  }
  if (!saved.data) return <p className="text-xs text-gray-400">Loading the paper…</p>
  if (saved.data.kind !== 'paper') {
    return (
      <EmptyState
        icon="fa-solid fa-table-cells-large"
        title="This is a schema"
        action={
          <Link to={`/sme/schemas/${saved.data.id}`} className={link}>
            Open it in Manage Schema
          </Link>
        }
      >
        {saved.data.title} is a schema, not a question paper: generate a paper from it there.
      </EmptyState>
    )
  }
  return <PaperEditor key={saved.data.id} saved={saved.data} locked={saved.data.structure_locked} />
}

function PaperEditor({ saved, locked }: { saved: Blueprint; locked: boolean }) {
  const { form, setForm, verdict, dirty, version, busy, save } = useBlueprintEditor(saved, () =>
    initialForm('paper'),
  )
  const descriptors = useDescriptors()
  const [previewing, setPreviewing] = useState(false)

  const actions = (
    <>
      <span className="self-center text-[11px] text-gray-400" aria-live="polite">
        Version {version}
        {dirty ? ' · unsaved changes' : ' · saved'}
      </span>
      <SmallButton
        icon="fa-solid fa-floppy-disk"
        tone="purple"
        disabled={verdict.state !== 'valid' || !dirty || busy}
        onClick={() => void save()}
      >
        Save paper
      </SmallButton>
      <SmallButton icon="fa-solid fa-eye" disabled={dirty} onClick={() => setPreviewing(true)}>
        Preview
      </SmallButton>
    </>
  )

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs text-gray-400">
        <Link to="/sme/papers" className="hover:text-white">
          Manage Question Papers
        </Link>{' '}
        › <span className="text-gray-200">{form.title.trim() || saved.title}</span>
      </nav>
      <h1 className="sr-only">Edit question paper {saved.title}</h1>
      {locked && (
        <p
          role="note"
          className="rounded-xl border border-amber-500/40 bg-amber-950/40 p-3 text-xs text-amber-200"
        >
          <i className="fa-solid fa-lock" aria-hidden="true" /> Booklets were uploaded against this
          paper, so its structure is locked: you may change only the title, descriptor, description,
          target batch, duration and course code. Clone it on Manage Question Papers to change the
          structure. Questions, keys and rubrics stay editable.
        </p>
      )}
      {dirty && !locked && (
        <p className="text-xs text-gray-400">
          Save the paper to preview it: the preview shows the saved version.
        </p>
      )}
      <Designer
        form={form}
        setForm={setForm}
        verdict={verdict}
        actions={actions}
        descriptors={descriptors}
        locked={locked}
      />
      {previewing && (
        <PaperPreview
          paper={{
            id: saved.id,
            version,
            title: form.title || saved.title,
            exam_code: form.examCode,
          }}
          onClose={() => setPreviewing(false)}
        />
      )}
    </div>
  )
}
