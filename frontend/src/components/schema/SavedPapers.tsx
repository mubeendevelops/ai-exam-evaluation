import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { DeleteContentDialog } from '../app/DeleteContentDialog'
import { Badge, GlassPanel, useToast } from '../ui'
import { SmallButton } from './controls'

type Paper = components['schemas']['BlueprintSummaryOut']

const open =
  'inline-flex items-center gap-1 rounded-lg border border-cyan-500/40 bg-cyan-950 px-2.5 py-1 text-xs text-cyan-300 hover:bg-cyan-900/50'

/**
 * The papers and schemas of the SME's subjects: Open loads one into the designer (P33, O20);
 * Delete is permanent (P28, D166), allowed once no unit that is not frozen uses the paper, and
 * the dialog says why when it is not. `openId`: the one in the designer now.
 */
export function SavedPapers({ openId = '' }: { openId?: string }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [deleting, setDeleting] = useState<Paper | null>(null)
  const papers = useQuery({
    queryKey: ['blueprints'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/blueprints')
      if (!data) throw new Error('papers unavailable')
      return data
    },
  })
  if (!papers.data || papers.data.length === 0) return null
  return (
    <GlassPanel as="section" aria-labelledby="saved-papers-title" className="space-y-3 p-6">
      <h2
        id="saved-papers-title"
        className="flex items-center gap-2 text-sm font-bold tracking-wider text-purple-400 uppercase"
      >
        <i className="fa-solid fa-box-archive" aria-hidden="true" /> Saved papers
      </h2>
      <ul className="space-y-2">
        {papers.data.map((p) => (
          <li
            key={p.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-gray-800 p-3 text-xs"
          >
            <span className="text-gray-200">
              <Badge tone={p.kind === 'schema' ? 'indigo' : 'purple'}>
                {p.kind === 'schema' ? 'Schema' : 'Paper'}
              </Badge>{' '}
              {p.exam_code && <span className="mr-2 font-mono text-purple-300">{p.exam_code}</span>}
              {p.title} · {p.subject_name ?? 'subject'} · v{p.version} · {p.total_marks} marks
            </span>
            <span className="flex gap-2">
              {p.id === openId ? (
                <span className="self-center text-[11px] text-gray-400">Open above</span>
              ) : (
                <Link
                  to={`/schema?blueprint=${p.id}`}
                  className={open}
                  aria-label={`Open ${p.title}`}
                >
                  <i className="fa-solid fa-folder-open" aria-hidden="true" /> Open
                </Link>
              )}
              <SmallButton icon="fa-solid fa-trash" tone="gray" onClick={() => setDeleting(p)}>
                Delete
              </SmallButton>
            </span>
          </li>
        ))}
      </ul>
      {deleting && (
        <DeleteContentDialog
          kind={deleting.kind === 'schema' ? 'schema' : 'paper'}
          id={deleting.id}
          name={deleting.title}
          onClose={() => setDeleting(null)}
          onDeleted={async () => {
            toast.show(`${deleting.title} deleted.`)
            setDeleting(null)
            await queryClient.invalidateQueries({ queryKey: ['blueprints'] })
          }}
        />
      )}
    </GlassPanel>
  )
}
