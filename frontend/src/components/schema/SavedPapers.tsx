import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { DeleteContentDialog } from '../app/DeleteContentDialog'
import { GlassPanel, useToast } from '../ui'
import { SmallButton } from './controls'

type Paper = components['schemas']['BlueprintSummaryOut']

/**
 * The college's papers and schemas in the SME's subjects, with a permanent Delete (P28, D166).
 * Loading a paper into the designer is still to come (O20); deleting is allowed once no unit
 * that is not frozen uses the paper, and the dialog says why when it is not.
 */
export function SavedPapers() {
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
              {p.exam_code && <span className="mr-2 font-mono text-purple-300">{p.exam_code}</span>}
              {p.title} · {p.subject_name ?? 'subject'} · v{p.version} · {p.total_marks} marks
            </span>
            <SmallButton icon="fa-solid fa-trash" tone="gray" onClick={() => setDeleting(p)}>
              Delete
            </SmallButton>
          </li>
        ))}
      </ul>
      {deleting && (
        <DeleteContentDialog
          kind="paper"
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
