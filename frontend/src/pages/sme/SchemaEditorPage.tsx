import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { SmallButton } from '../../components/schema/controls'
import { Designer } from '../../components/schema/Designer'
import {
  useBlueprintEditor,
  useSavedBlueprint,
  type Blueprint,
} from '../../components/schema/saved'
import { paperEditorPath, useSchemas } from '../../components/sme/schemas'
import { EmptyState, useToast } from '../../components/ui'
import { initialForm } from '../../lib/blueprint'

const link =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700'

/**
 * One schema in the designer (`/sme/schemas/:blueprintId`, P33): schema mode (no exam code,
 * no question links, the 1.2 header). Save stores the next version; Generate Question Paper
 * makes a paper draft of the saved version (D220).
 */
export default function SchemaEditorPage() {
  const { blueprintId = '' } = useParams()
  const saved = useSavedBlueprint(blueprintId)

  if (saved.isError) {
    return (
      <EmptyState
        icon="fa-solid fa-table-cells-large"
        title="Schema not found"
        action={
          <Link to="/sme/schemas" className={link}>
            Back to Manage Schema
          </Link>
        }
      >
        It is not in your subjects, or it has been deleted.
      </EmptyState>
    )
  }
  if (!saved.data) return <p className="text-xs text-gray-400">Loading the schema…</p>
  if (saved.data.kind !== 'schema') {
    return (
      <EmptyState
        icon="fa-solid fa-file-lines"
        title="This is a question paper"
        action={
          <Link to={paperEditorPath(saved.data.id)} className={link}>
            Open it in the Schema Designer
          </Link>
        }
      >
        {saved.data.title} is a paper, not a schema; papers are edited in the Schema Designer for
        now.
      </EmptyState>
    )
  }
  return <SchemaEditor key={saved.data.id} saved={saved.data} />
}

function SchemaEditor({ saved }: { saved: Blueprint }) {
  const toast = useToast()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { form, setForm, verdict, dirty, version, busy, save } = useBlueprintEditor(saved, () =>
    initialForm('schema'),
  )
  const batches = useSchemas().data?.batches ?? []
  const [generating, setGenerating] = useState(false)

  async function generate() {
    setGenerating(true)
    try {
      const { data, error } = await api.POST('/api/v1/blueprints/{blueprint_id}/paper', {
        params: { path: { blueprint_id: saved.id } },
      })
      if (!data) {
        toast.show(problemOf(error, 'Could not generate the paper.').message, 'red')
        return
      }
      await queryClient.invalidateQueries({ queryKey: ['schemas'] })
      toast.show(`${data.title} made: link its questions and save.`)
      await navigate(paperEditorPath(data.id))
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
    } finally {
      setGenerating(false)
    }
  }

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
        Save schema
      </SmallButton>
      <SmallButton
        icon="fa-solid fa-wand-magic-sparkles"
        disabled={dirty || generating}
        onClick={() => void generate()}
      >
        Generate Question Paper
      </SmallButton>
    </>
  )

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs text-gray-400">
        <Link to="/sme/schemas" className="hover:text-white">
          Manage Schema
        </Link>{' '}
        › <span className="text-gray-200">{form.title.trim() || saved.title}</span>
      </nav>
      <h1 className="sr-only">Edit schema {saved.title}</h1>
      {dirty && (
        <p className="text-xs text-gray-400">
          Save the schema before generating a paper from it: the paper copies the saved version.
        </p>
      )}
      <Designer
        form={form}
        setForm={setForm}
        verdict={verdict}
        actions={actions}
        batches={batches}
      />
    </div>
  )
}
