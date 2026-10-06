import { useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { useDebounced } from '../../hooks/useDebounced'
import { inputClass } from '../schema/controls'

export type Student = components['schemas']['StudentOut']

/** Finds a student of the college's roster by USN or name. */
export function StudentPicker({
  value,
  onChange,
}: {
  value: Student | null
  onChange: (student: Student | null) => void
}) {
  const id = useId()
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const query = useDebounced(text.trim(), 250)

  const found = useQuery({
    queryKey: ['students', query],
    enabled: open,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/students', {
        params: { query: { q: query, limit: 8 } },
      })
      if (!data) throw new Error('roster unavailable')
      return data
    },
    placeholderData: (previous) => previous,
  })

  if (value) {
    return (
      <div>
        <span className="mb-1 block text-xs font-semibold text-gray-300">Student</span>
        <div className="flex items-center justify-between gap-2 rounded-xl border border-purple-500/40 bg-purple-950/40 px-3 py-2 text-xs">
          <span className="text-white">
            <strong>{value.name}</strong>
            <span className="ml-2 font-mono text-gray-300">{value.usn}</span>
            {value.class_section && (
              <span className="ml-2 text-gray-400">{value.class_section}</span>
            )}
          </span>
          <button
            type="button"
            onClick={() => {
              onChange(null)
              setText('')
            }}
            className="rounded-md px-2 py-0.5 text-gray-300 hover:text-white"
            aria-label={`Choose another student than ${value.name}`}
          >
            <i className="fa-solid fa-xmark" aria-hidden="true" /> Change
          </button>
        </div>
      </div>
    )
  }

  const listId = `${id}-list`
  const students = found.data ?? []
  return (
    <div className="relative">
      <label htmlFor={id} className="mb-1 block text-xs font-semibold text-gray-300">
        Student
      </label>
      <input
        id={id}
        type="search"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        placeholder="Search the roster by USN or name"
        className={inputClass}
        value={text}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
        }}
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Students"
          className="absolute z-20 mt-1 max-h-60 w-full overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-1 shadow-xl"
        >
          {found.isError && (
            <li className="px-3 py-2 text-xs text-red-400">The roster could not be read.</li>
          )}
          {!found.isError && students.length === 0 && (
            <li className="px-3 py-2 text-xs text-gray-400">
              {found.isPending
                ? 'Searching…'
                : 'No student matches. Import the roster in the Admin tab.'}
            </li>
          )}
          {students.map((s) => (
            <li key={s.id} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={false}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(s)
                  setOpen(false)
                }}
                className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-1.5 text-left text-xs hover:bg-gray-800"
              >
                <span className="text-white">{s.name}</span>
                <span className="font-mono text-gray-400">{s.usn}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
