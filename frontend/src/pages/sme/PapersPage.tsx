import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { DeleteContentDialog } from '../../components/app/DeleteContentDialog'
import { SelectInput, TextInput } from '../../components/schema/controls'
import { PaperDialog } from '../../components/sme/PaperDialog'
import { PaperPreview, useDownloadPaper } from '../../components/sme/PaperPreview'
import { durationOf, PAPERS_PAGE, usePapers, type PaperCard } from '../../components/sme/papers'
import { updatedOf } from '../../components/sme/schemas'
import { Badge, EmptyState, GlassPanel, useToast } from '../../components/ui'
import { PrimaryButton, RowButton } from '../admin/controls'

type Dialog =
  | { kind: 'create' }
  | { kind: 'clone'; card: PaperCard }
  | { kind: 'delete'; card: PaperCard }
  | { kind: 'preview'; card: PaperCard }

const link =
  'inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700'

/** `YYYY-MM-DD` or '' (anything else in the address is ignored). */
function dateParam(value: string | null): string {
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : ''
}

/**
 * Manage Question Papers (P34, spec 3.2, mock tab 2): the papers of the SME's subjects as
 * cards, searched by name or descriptor, filtered by creation date and descriptor (`?q=&date=
 * &descriptor=&page=`), a page of twelve at a time. Create and Clone are mock modal 6; Edit opens
 * the designer in paper mode; Preview is modal 5 (the PDF with zoom); Delete is permanent once no
 * unfrozen unit uses the paper (D166).
 */
export default function PapersPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const descriptor = params.get('descriptor') ?? ''
  const date = dateParam(params.get('date'))
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1)
  const [text, setText] = useState(q)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const papers = usePapers({ q, descriptor, date, page })
  const { download, busy } = useDownloadPaper()

  function update(change: { q?: string; descriptor?: string; date?: string; page?: number }) {
    setParams(
      (old) => {
        const next = new URLSearchParams(old)
        for (const [key, value] of Object.entries(change)) {
          if (value && !(key === 'page' && value === 1)) next.set(key, String(value))
          else next.delete(key)
        }
        // A new filter starts again at the first page.
        if (change.page === undefined) next.delete('page')
        return next
      },
      { replace: true },
    )
  }

  // The search box filters as you type, a moment after the last key.
  useEffect(() => {
    if (text.trim() === q.trim()) return
    const timer = setTimeout(() => update({ q: text.trim() }), 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `update` is rebuilt each render
  }, [text, q])

  const items = papers.data?.items ?? []
  const total = papers.data?.total ?? 0
  const descriptors = papers.data?.descriptors ?? []
  const filtered = q !== '' || descriptor !== '' || date !== ''
  const first = (page - 1) * PAPERS_PAGE + 1
  const last = first + items.length - 1
  const pages = Math.max(1, Math.ceil(total / PAPERS_PAGE))

  return (
    <div className="space-y-6">
      <h1 className="sr-only">Manage Question Papers</h1>
      <GlassPanel as="section" aria-labelledby="papers-heading" className="space-y-5 p-6">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div>
            <h2 id="papers-heading" className="text-base font-semibold text-white">
              Manage Question Papers
            </h2>
            <p className="mt-1 text-xs text-gray-400">
              Directory of your subjects' question papers: draft, link their questions, preview and
              download them.
            </p>
          </div>
          <PrimaryButton onClick={() => setDialog({ kind: 'create' })}>
            <i className="fa-solid fa-plus" aria-hidden="true" /> Create Question Paper
          </PrimaryButton>
        </div>

        <div className="grid gap-3 rounded-xl border border-gray-800 bg-gray-950/60 p-3.5 md:grid-cols-[2fr_1fr_1fr]">
          <TextInput
            label="Search by paper name or descriptor"
            type="search"
            value={text}
            maxLength={200}
            placeholder="e.g. Mid term 2026 batch"
            onChange={(e) => setText(e.target.value)}
          />
          <TextInput
            label="Created on"
            type="date"
            value={date}
            onChange={(e) => update({ date: e.target.value })}
          />
          <SelectInput
            label="Filter Tag"
            value={descriptor}
            onChange={(e) => update({ descriptor: e.target.value })}
          >
            <option value="">All Descriptors</option>
            {descriptor && !descriptors.includes(descriptor) && (
              <option value={descriptor}>{descriptor}</option>
            )}
            {descriptors.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </SelectInput>
        </div>

        {papers.isError && (
          <p role="alert" className="text-xs text-red-300">
            Your question papers could not be loaded.
          </p>
        )}
        {papers.isPending && <p className="text-xs text-gray-400">Loading your papers…</p>}

        {papers.data && total === 0 && !filtered && (
          <EmptyState
            icon="fa-solid fa-box-archive"
            title="No question papers yet"
            action={
              <RowButton onClick={() => setDialog({ kind: 'create' })}>
                Create Question Paper
              </RowButton>
            }
          >
            Create one from scratch, or generate one from a schema on{' '}
            <Link to="/sme/schemas" className="text-cyan-300 hover:underline">
              Manage Schema
            </Link>
            .
          </EmptyState>
        )}
        {papers.data && total === 0 && filtered && (
          <EmptyState
            icon="fa-solid fa-filter-circle-xmark"
            title="No paper matches"
            action={
              <RowButton
                onClick={() => {
                  setText('')
                  update({ q: '', descriptor: '', date: '' })
                }}
              >
                Clear the filters
              </RowButton>
            }
          />
        )}
        {papers.data && total > 0 && items.length === 0 && (
          <EmptyState icon="fa-solid fa-book-open" title="No papers on this page">
            <RowButton onClick={() => update({ page: 1 })}>Back to the first page</RowButton>
          </EmptyState>
        )}

        {items.length > 0 && (
          <ul aria-label="Question papers" className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {items.map((card) => (
              <PaperCardView
                key={card.id}
                card={card}
                downloading={busy === card.id}
                onClone={() => setDialog({ kind: 'clone', card })}
                onDelete={() => setDialog({ kind: 'delete', card })}
                onPreview={() => setDialog({ kind: 'preview', card })}
                onDownload={() => void download(card)}
              />
            ))}
          </ul>
        )}
        {items.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-800/80 pt-3 text-xs text-gray-400">
            <span aria-live="polite">
              Showing{' '}
              <strong className="text-white">
                {first} - {last}
              </strong>{' '}
              of <strong className="text-white">{total}</strong> Question{' '}
              {total === 1 ? 'Paper' : 'Papers'}
            </span>
            {pages > 1 && (
              <nav aria-label="Pages" className="flex items-center gap-2">
                <RowButton disabled={page <= 1} onClick={() => update({ page: page - 1 })}>
                  Previous
                </RowButton>
                <span>
                  Page {page} of {pages}
                </span>
                <RowButton disabled={page >= pages} onClick={() => update({ page: page + 1 })}>
                  Next
                </RowButton>
              </nav>
            )}
          </div>
        )}
      </GlassPanel>

      {dialog?.kind === 'create' && (
        <PaperDialog
          descriptors={descriptors}
          onClose={() => setDialog(null)}
          onSaved={async (made) => {
            setDialog(null)
            await navigate(`/sme/papers/${made.id}`)
          }}
        />
      )}
      {dialog?.kind === 'clone' && (
        <PaperDialog
          source={dialog.card}
          descriptors={descriptors}
          onClose={() => setDialog(null)}
          onSaved={async (made) => {
            setDialog(null)
            await navigate(`/sme/papers/${made.id}`)
          }}
        />
      )}
      {dialog?.kind === 'preview' && (
        <PaperPreview paper={dialog.card} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteContentDialog
          kind="paper"
          id={dialog.card.id}
          name={dialog.card.title}
          onClose={() => setDialog(null)}
          onDeleted={async () => {
            toast.show(`${dialog.card.title} deleted.`)
            setDialog(null)
            await Promise.all([
              queryClient.invalidateQueries({ queryKey: ['papers'] }),
              queryClient.invalidateQueries({ queryKey: ['schemas'] }),
            ])
          }}
        />
      )}
    </div>
  )
}

function PaperCardView({
  card,
  downloading,
  onClone,
  onDelete,
  onPreview,
  onDownload,
}: {
  card: PaperCard
  downloading: boolean
  onClone: () => void
  onDelete: () => void
  onPreview: () => void
  onDownload: () => void
}) {
  const headingId = `paper-${card.id}`
  const duration = durationOf(card.duration_minutes)
  const updated = updatedOf(card.updated_at)
  return (
    <li
      aria-labelledby={headingId}
      className="flex flex-col justify-between rounded-xl border border-gray-800 bg-gray-950/80 p-4 transition hover:border-gray-700"
    >
      <div>
        <div className="mb-3 flex items-center justify-between gap-2">
          {card.subject_name ? <Badge tone="indigo">{card.subject_name}</Badge> : <span />}
          <span className="text-xs text-gray-500">
            {[duration, `${card.total_marks} Marks`].filter(Boolean).join(' • ')}
          </span>
        </div>
        <h3 id={headingId} className="mb-1 text-sm font-semibold text-white">
          {card.title}
        </h3>
        <p className="mb-2 line-clamp-2 text-xs text-gray-400">
          {card.description || 'No description yet.'}
        </p>
        <p className="mb-1 flex items-center gap-1 text-[11px] font-medium text-indigo-300">
          <i className="fa-solid fa-bars-staggered" aria-hidden="true" /> Descriptor:{' '}
          <span className="text-amber-400">{card.descriptor || 'none'}</span>
        </p>
        <p className="mb-4 text-[11px] text-gray-500">
          {card.exam_code ? `Exam code ${card.exam_code}` : 'No exam code yet'}
          {card.in_use && ' · In use by a unit'}
          {updated && ` · Updated ${updated}`}
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-800 pt-3 text-xs">
        <div className="flex flex-wrap gap-2">
          <RowButton onClick={onClone} aria-label={`Clone ${card.title}`}>
            <i className="fa-regular fa-copy" aria-hidden="true" />
          </RowButton>
          <Link to={`/sme/papers/${card.id}`} className={link} aria-label={`Edit ${card.title}`}>
            <i className="fa-solid fa-pen" aria-hidden="true" /> Edit
          </Link>
          <RowButton onClick={onDelete} aria-label={`Delete ${card.title}`}>
            <i className="fa-solid fa-trash" aria-hidden="true" />
          </RowButton>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onPreview}
            aria-label={`Preview ${card.title}`}
            className="rounded border border-indigo-800/60 bg-indigo-950/60 px-2.5 py-1 font-medium text-indigo-300 hover:bg-indigo-900/60"
          >
            Preview
          </button>
          <button
            type="button"
            onClick={onDownload}
            disabled={downloading}
            aria-label={`Download the PDF of ${card.title}`}
            className="rounded bg-gray-800 px-2.5 py-1 font-medium text-gray-200 hover:bg-gray-700 disabled:opacity-60"
          >
            PDF ↓
          </button>
        </div>
      </div>
    </li>
  )
}
