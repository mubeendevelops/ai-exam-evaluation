import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { DeleteContentDialog } from '../../components/app/DeleteContentDialog'
import { SelectInput, TextInput } from '../../components/schema/controls'
import { SchemaDialog } from '../../components/sme/SchemaDialog'
import {
  paperEditorPath,
  updatedOf,
  useSchemas,
  type SchemaCard,
} from '../../components/sme/schemas'
import { Badge, EmptyState, GlassPanel, useToast } from '../../components/ui'
import { PrimaryButton, RowButton } from '../admin/controls'

type Dialog =
  { kind: 'add' } | { kind: 'clone'; card: SchemaCard } | { kind: 'delete'; card: SchemaCard }

const link =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700'

/**
 * Manage Schema (P33, spec 3.3, mock tab 3): reusable paper templates of the SME's subjects as
 * cards, filtered by name and class / batch (`?q=&batch=`, the batches in use). Add New Schema
 * and Clone are mock modal 7; Edit opens the designer in schema mode; Delete is permanent
 * (D166); Generate Question Paper makes a paper draft from the schema and opens it (D220).
 */
export default function SchemasPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const batch = params.get('batch') ?? ''
  const [text, setText] = useState(q)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [generating, setGenerating] = useState('')
  const schemas = useSchemas(q, batch)

  function update(change: { q?: string; batch?: string }) {
    setParams(
      (old) => {
        const next = new URLSearchParams(old)
        for (const [key, value] of Object.entries(change)) {
          if (value) next.set(key, value)
          else next.delete(key)
        }
        return next
      },
      { replace: true },
    )
  }

  // The name box filters as you type, a moment after the last key.
  useEffect(() => {
    if (text.trim() === q.trim()) return
    const timer = setTimeout(() => update({ q: text.trim() }), 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `update` is rebuilt each render
  }, [text, q])

  async function generate(card: SchemaCard) {
    setGenerating(card.id)
    try {
      const { data, error } = await api.POST('/api/v1/blueprints/{blueprint_id}/paper', {
        params: { path: { blueprint_id: card.id } },
      })
      if (!data) {
        toast.show(problemOf(error, 'Could not generate the paper.').message, 'red')
        return
      }
      await queryClient.invalidateQueries({ queryKey: ['schemas'] })
      toast.show(`${data.title} made from ${card.title}: link its questions and save.`)
      await navigate(paperEditorPath(data.id))
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
    } finally {
      setGenerating('')
    }
  }

  const items = schemas.data?.items ?? []
  const batches = schemas.data?.batches ?? []
  const filtered = q !== '' || batch !== ''

  return (
    <div className="space-y-6">
      <h1 className="sr-only">Manage Schema</h1>
      <GlassPanel as="section" aria-labelledby="schemas-heading" className="space-y-5 p-6">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div>
            <h2 id="schemas-heading" className="text-base font-semibold text-white">
              Manage Schema
            </h2>
            <p className="mt-1 text-xs text-gray-400">
              Reusable paper templates: sections, choice rules, marks and rubric weightages.
              Generate a question paper from one, then link its questions.
            </p>
          </div>
          <PrimaryButton onClick={() => setDialog({ kind: 'add' })}>
            <i className="fa-solid fa-plus" aria-hidden="true" /> Add New Schema
          </PrimaryButton>
        </div>

        <div className="grid gap-3 rounded-xl border border-gray-800 bg-gray-950/60 p-3.5 md:grid-cols-2">
          <TextInput
            label="Search schema by name"
            type="search"
            value={text}
            maxLength={200}
            onChange={(e) => setText(e.target.value)}
          />
          <SelectInput
            label="Class / Batch"
            value={batch}
            onChange={(e) => update({ batch: e.target.value })}
          >
            <option value="">All Classes / Batches</option>
            {batch && !batches.includes(batch) && <option value={batch}>{batch}</option>}
            {batches.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </SelectInput>
        </div>

        {schemas.isError && (
          <p role="alert" className="text-xs text-red-300">
            Your schemas could not be loaded.
          </p>
        )}
        {schemas.isPending && <p className="text-xs text-gray-400">Loading your schemas…</p>}

        {schemas.data && items.length === 0 && !filtered && (
          <EmptyState
            icon="fa-solid fa-table-cells-large"
            title="No schemas yet"
            action={
              <RowButton onClick={() => setDialog({ kind: 'add' })}>Add New Schema</RowButton>
            }
          >
            A schema lays out a paper's sections, choice rules and marks once, for every paper you
            make from it.
          </EmptyState>
        )}
        {schemas.data && items.length === 0 && filtered && (
          <EmptyState
            icon="fa-solid fa-filter-circle-xmark"
            title="No schema matches"
            action={
              <RowButton
                onClick={() => {
                  setText('')
                  update({ q: '', batch: '' })
                }}
              >
                Clear the filter
              </RowButton>
            }
          />
        )}

        {items.length > 0 && (
          <ul aria-label="Schemas" className="grid gap-4 md:grid-cols-2">
            {items.map((card) => (
              <SchemaCardView
                key={card.id}
                card={card}
                generating={generating === card.id}
                onClone={() => setDialog({ kind: 'clone', card })}
                onDelete={() => setDialog({ kind: 'delete', card })}
                onGenerate={() => void generate(card)}
              />
            ))}
          </ul>
        )}
        {items.length > 0 && (
          <p className="text-xs text-gray-400" aria-live="polite">
            Showing {items.length} {items.length === 1 ? 'schema' : 'schemas'}
          </p>
        )}
      </GlassPanel>

      {dialog?.kind === 'add' && (
        <SchemaDialog
          batches={batches}
          onClose={() => setDialog(null)}
          onSaved={async (made) => {
            setDialog(null)
            await navigate(`/sme/schemas/${made.id}`)
          }}
        />
      )}
      {dialog?.kind === 'clone' && (
        <SchemaDialog
          source={dialog.card}
          batches={batches}
          onClose={() => setDialog(null)}
          onSaved={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteContentDialog
          kind="schema"
          id={dialog.card.id}
          name={dialog.card.title}
          onClose={() => setDialog(null)}
          onDeleted={async () => {
            toast.show(`${dialog.card.title} deleted.`)
            setDialog(null)
            await queryClient.invalidateQueries({ queryKey: ['schemas'] })
          }}
        />
      )}
    </div>
  )
}

function SchemaCardView({
  card,
  generating,
  onClone,
  onDelete,
  onGenerate,
}: {
  card: SchemaCard
  generating: boolean
  onClone: () => void
  onDelete: () => void
  onGenerate: () => void
}) {
  const headingId = `schema-${card.id}`
  const updated = updatedOf(card.updated_at)
  const text = card.weightage_note || card.description
  return (
    <li
      aria-labelledby={headingId}
      className="flex flex-col justify-between rounded-xl border border-gray-800 bg-gray-950/60 p-4 transition hover:border-gray-700"
    >
      <div>
        <div className="mb-2 flex items-start justify-between gap-2">
          <div>
            <h3 id={headingId} className="text-sm font-semibold text-white">
              {card.title}
            </h3>
            <span className="text-[11px] font-medium text-indigo-400">
              {card.target_batch ? `Batch: ${card.target_batch}` : 'No batch set'}
            </span>
          </div>
          {card.subject_name && <Badge tone="cyan">{card.subject_name}</Badge>}
        </div>
        <p className="mb-3 text-xs text-gray-400">{text || 'No weightage description yet.'}</p>
        <p className="mb-4 text-[11px] text-gray-500">
          {card.total_marks} marks · {card.section_count}{' '}
          {card.section_count === 1 ? 'section' : 'sections'} · Used in {card.used_in} Question{' '}
          {card.used_in === 1 ? 'Paper' : 'Papers'}
          {updated && ` · Updated ${updated}`}
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-800/80 pt-3">
        <div className="flex flex-wrap gap-2">
          <RowButton onClick={onClone} aria-label={`Clone ${card.title}`}>
            <i className="fa-regular fa-copy" aria-hidden="true" /> Clone
          </RowButton>
          <Link to={`/sme/schemas/${card.id}`} className={link} aria-label={`Edit ${card.title}`}>
            <i className="fa-solid fa-pen" aria-hidden="true" /> Edit
          </Link>
          <RowButton onClick={onDelete} aria-label={`Delete ${card.title}`}>
            <i className="fa-solid fa-trash" aria-hidden="true" /> Delete
          </RowButton>
        </div>
        <button
          type="button"
          disabled={generating}
          onClick={onGenerate}
          aria-label={`Generate Question Paper from ${card.title}`}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-60"
        >
          <i className="fa-solid fa-wand-magic-sparkles" aria-hidden="true" />
          {generating ? 'Generating…' : 'Generate Question Paper'}
        </button>
      </div>
    </li>
  )
}
