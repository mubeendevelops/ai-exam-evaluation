import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { useDebounced } from '../../hooks/useDebounced'
import { editorUrl, type EditorContext } from '../../lib/editorLinks'
import { compactInputClass } from '../schema/controls'
import { GlassPanel } from '../ui'

const SHOWN = 8

/**
 * The bank search beside the editor (D165: "a search used while creating questions"): questions
 * of the SME's subjects whose code, text, topic or key match, to open, or to clone as the start
 * of a new question. `context` (the way back to a paper's slot) is carried to both.
 */
export function SimilarPanel({
  exclude,
  context,
}: {
  /** The question being edited: never listed. */
  exclude?: string
  context: EditorContext
}) {
  const [text, setText] = useState('')
  const keyword = useDebounced(text.trim(), 300)
  const inputId = useId()
  const found = useQuery({
    queryKey: ['question-search', 'similar', keyword],
    enabled: keyword.length >= 2,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/questions', {
        params: { query: { keyword, limit: SHOWN + 1 } },
      })
      if (!data) throw new Error('search unavailable')
      return data
    },
    placeholderData: keepPreviousData,
  })
  const items = (found.data?.items ?? []).filter((q) => q.id !== exclude).slice(0, SHOWN)
  const carried = { return: context.return, slot: context.slot, marks: context.marks }

  return (
    <GlassPanel as="aside" aria-label="Similar questions" className="space-y-3 p-4">
      <h2 className="flex items-center gap-2 text-sm font-bold tracking-wider text-cyan-400 uppercase">
        <i className="fa-solid fa-magnifying-glass" aria-hidden="true" /> Similar questions
      </h2>
      <label htmlFor={inputId} className="block text-xs text-gray-300">
        Search your subjects' questions by code, text, topic or key
      </label>
      <input
        id={inputId}
        type="search"
        className={compactInputClass}
        value={text}
        maxLength={200}
        placeholder="e.g. induced emf"
        onChange={(e) => setText(e.target.value)}
      />
      {keyword.length < 2 && (
        <p className="text-[11px] text-gray-400">
          Find a question to open, or to clone as the start of a new one, before writing it again.
        </p>
      )}
      {found.isError && (
        <p role="alert" className="text-xs text-red-300">
          The question bank could not be searched.
        </p>
      )}
      {found.data && items.length === 0 && (
        <p className="text-xs text-gray-400">No other question matches.</p>
      )}
      {items.length > 0 && (
        <ul aria-label="Matching questions" className="space-y-2">
          {items.map((q) => (
            <li
              key={q.id}
              className="space-y-1.5 rounded-lg border border-gray-800 bg-gray-950/60 p-2.5"
            >
              <p className="text-xs">
                <span className="font-mono font-semibold text-cyan-300">{q.code}</span>{' '}
                <span className="text-gray-400">
                  · {q.max_marks} {q.max_marks === 1 ? 'mark' : 'marks'}
                  {q.subject_name ? ` · ${q.subject_name}` : ''}
                </span>
              </p>
              <p className="line-clamp-2 text-xs text-gray-300">{q.text}</p>
              <div className="flex gap-2">
                <Link
                  to={editorUrl(q.id, carried)}
                  aria-label={`Open ${q.code}`}
                  className="rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700"
                >
                  Open
                </Link>
                <Link
                  to={editorUrl(null, { ...carried, from: q.id })}
                  aria-label={`Clone ${q.code} as a start`}
                  className="rounded-lg border border-purple-500/40 bg-purple-950/40 px-2.5 py-1 text-[11px] font-semibold text-purple-200 hover:bg-purple-900/50"
                >
                  Clone as start
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
      {(found.data?.total ?? 0) > SHOWN && (
        <p className="text-[11px] text-gray-400">More match: narrow the search.</p>
      )}
    </GlassPanel>
  )
}
