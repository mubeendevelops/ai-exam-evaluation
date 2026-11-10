import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import { useDebounced } from '../../hooks/useDebounced'
import { useSearch, type SearchResults } from './notifications'

interface Option {
  key: string
  group: string
  label: string
  detail: string
  to: string
}

const GROUPS: readonly [keyof SearchResults, string][] = [
  ['units', 'Units'],
  ['students', 'Students'],
  ['papers', 'Question papers'],
  ['schemas', 'Schemas'],
  ['questions', 'Questions'],
]

/** Each hit as one option with the screen it opens. */
export function optionsOf(found: SearchResults): Option[] {
  const out: Option[] = []
  for (const u of found.units)
    out.push({
      key: `u-${u.unit_id}`,
      group: 'units',
      label: `${u.code} · ${u.paper_title || 'Paper deleted'}`,
      detail: [u.exam_code, u.year, `due ${u.due_date}`].filter(Boolean).join(' · '),
      to: `/sme/units/${u.unit_id}`,
    })
  for (const s of found.students)
    out.push({
      key: `s-${s.booklet_id}`,
      group: 'students',
      label: s.name,
      detail: s.usn,
      to: `/sme/units/${s.unit_id}/booklets/${s.booklet_id}`,
    })
  for (const p of found.papers)
    out.push({
      key: `p-${p.blueprint_id}`,
      group: 'papers',
      label: p.title,
      detail: [p.exam_code, p.descriptor].filter(Boolean).join(' · '),
      to: `/sme/papers/${p.blueprint_id}`,
    })
  for (const p of found.schemas)
    out.push({
      key: `c-${p.blueprint_id}`,
      group: 'schemas',
      label: p.title,
      detail: p.descriptor,
      to: `/sme/schemas/${p.blueprint_id}`,
    })
  for (const q of found.questions)
    out.push({
      key: `q-${q.question_id}`,
      group: 'questions',
      label: q.code,
      detail: q.text,
      to: `/sme/questions/${q.question_id}`,
    })
  return out
}

/** The SME header's search (P37, D228): an ARIA combobox over one server call, grouped results,
 * arrows to move, Enter to open, Escape to close. 2 to 80 characters. */
export function GlobalSearch() {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const root = useRef<HTMLDivElement>(null)
  const listId = useId()
  const navigate = useNavigate()
  const query = useDebounced(text.trim(), 250)
  const search = useSearch(query)
  const options = useMemo(() => (search.data ? optionsOf(search.data) : []), [search.data])
  const ready = query.length >= 2 && query === text.trim()
  const shown = open && text.trim().length >= 2

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const go = (option: Option) => {
    setOpen(false)
    setText('')
    void navigate(option.to)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      if (options.length === 0) return
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((i) =>
        i < 0 && step < 0 ? options.length - 1 : (i + step + options.length) % options.length,
      )
    } else if (event.key === 'Enter') {
      const chosen = options[active] ?? (active < 0 ? options[0] : undefined)
      if (shown && chosen) {
        event.preventDefault()
        go(chosen)
      }
    } else if (event.key === 'Escape') {
      if (open) {
        event.preventDefault()
        setOpen(false)
        setActive(-1)
      } else {
        setText('')
      }
    }
  }

  const optionId = (i: number) => `${listId}-o${i}`

  return (
    <div ref={root} className="relative hidden sm:block">
      <input
        type="search"
        role="combobox"
        aria-label="Search assignments, students, papers, schemas and questions"
        aria-autocomplete="list"
        aria-expanded={shown}
        aria-controls={listId}
        aria-activedescendant={shown && active >= 0 ? optionId(active) : undefined}
        placeholder="Search assignments, schemas..."
        maxLength={80}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          setActive(-1)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        className="w-56 rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-1.5 text-xs text-gray-200 placeholder:text-gray-500 focus:border-purple-500 focus:outline-none lg:w-72"
      />
      <div
        id={listId}
        role="listbox"
        aria-label="Search results"
        hidden={!shown}
        className="glass-panel absolute right-0 z-50 mt-2 max-h-[28rem] w-[22rem] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-gray-700 py-1 shadow-2xl"
      >
        {shown && !ready && <p className="px-4 py-3 text-xs text-gray-400">Searching…</p>}
        {shown && ready && search.isFetching && !search.data && (
          <p className="px-4 py-3 text-xs text-gray-400">Searching…</p>
        )}
        {shown && ready && search.isError && (
          <p role="alert" className="px-4 py-3 text-xs text-red-300">
            The search could not be run. Try again in a moment.
          </p>
        )}
        {shown && ready && search.data && options.length === 0 && (
          <p className="px-4 py-3 text-xs text-gray-400">No matches.</p>
        )}
        {shown &&
          ready &&
          GROUPS.map(([group, heading]) => {
            const items = options.filter((o) => o.group === group)
            if (items.length === 0) return null
            const headingId = `${listId}-${group}`
            return (
              <div key={group} role="group" aria-labelledby={headingId}>
                <p
                  id={headingId}
                  className="px-4 pt-2 pb-1 text-[10px] font-semibold tracking-wider text-gray-500 uppercase"
                >
                  {heading}
                </p>
                {items.map((option) => {
                  const i = options.indexOf(option)
                  return (
                    <div
                      key={option.key}
                      id={optionId(i)}
                      role="option"
                      aria-selected={i === active}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => go(option)}
                      onMouseEnter={() => setActive(i)}
                      className={`cursor-pointer px-4 py-1.5 ${i === active ? 'bg-purple-900/40' : 'hover:bg-gray-800/60'}`}
                    >
                      <span className="block truncate text-xs text-gray-100">{option.label}</span>
                      {option.detail && (
                        <span className="block truncate text-[10px] text-gray-500">
                          {option.detail}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            )
          })}
      </div>
    </div>
  )
}
