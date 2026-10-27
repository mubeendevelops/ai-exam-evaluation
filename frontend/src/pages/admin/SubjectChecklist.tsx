import { useId } from 'react'
import type { Subject } from './data'

/** Subjects to tick for an SME. Retired subjects can only stay ticked or be unticked (D181). */
export function SubjectChecklist({
  subjects,
  selected,
  onChange,
}: {
  subjects: readonly Subject[]
  selected: ReadonlySet<string>
  onChange: (next: Set<string>) => void
}) {
  const prefix = useId()
  return (
    <fieldset className="space-y-2">
      <legend className="mb-1 text-xs font-bold tracking-wider text-gray-300 uppercase">
        Subjects
      </legend>
      {subjects.length === 0 && (
        <p className="text-xs text-gray-400">No subjects yet. Add a subject first.</p>
      )}
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {subjects.map((subject) => (
          <label
            key={subject.id}
            htmlFor={`${prefix}-${subject.id}`}
            className="flex items-center gap-2 rounded-lg border border-gray-700/80 bg-gray-950/60 px-3 py-2 text-xs text-gray-200"
          >
            <input
              id={`${prefix}-${subject.id}`}
              type="checkbox"
              checked={selected.has(subject.id)}
              onChange={(event) => {
                const next = new Set(selected)
                if (event.target.checked) next.add(subject.id)
                else next.delete(subject.id)
                onChange(next)
              }}
              className="h-3.5 w-3.5 accent-purple-500"
            />
            <span>
              {subject.name} <span className="font-mono text-gray-400">({subject.code})</span>
              {subject.retired && <span className="text-amber-300"> — retired</span>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
