import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { Field, compactInputClass, inputClass, SmallButton } from './controls'

interface Props {
  value: string
  onChange: (subjectId: string) => void
}

/** The subject of the paper: pick one (global list) or create one for my college. */
export function SubjectPicker({ value, onChange }: Props) {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const subjects = useQuery({
    queryKey: ['subjects'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/subjects')
      if (!data) throw new Error('subjects unavailable')
      return data
    },
  })

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST('/api/v1/subjects', {
        body: { code: code.trim(), name: name.trim() },
      })
      if (!data) throw new Error(problemOf(error, 'Could not create the subject.').message)
      return data
    },
    onSuccess: async (subject) => {
      await queryClient.invalidateQueries({ queryKey: ['subjects'] })
      onChange(subject.id)
      setAdding(false)
      setCode('')
      setName('')
      setProblem(null)
    },
    onError: (error) => setProblem(error instanceof Error ? error.message : NETWORK_PROBLEM),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setProblem(null)
    create.mutate()
  }

  return (
    <div className="space-y-2">
      <Field
        label="Subject"
        hint={
          subjects.isError ? (
            <span className="text-red-400">Could not load the subjects. Reload to try again.</span>
          ) : subjects.data?.length === 0 ? (
            'No subjects yet: create the first one.'
          ) : undefined
        }
      >
        {(id) => (
          <div className="flex gap-2">
            <select
              id={id}
              className={inputClass}
              value={value}
              onChange={(e) => onChange(e.target.value)}
            >
              <option value="">Select a subject…</option>
              {subjects.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.code})
                </option>
              ))}
            </select>
            <SmallButton icon="fa-solid fa-plus" tone="gray" onClick={() => setAdding(!adding)}>
              New
            </SmallButton>
          </div>
        )}
      </Field>
      {adding && (
        <form
          onSubmit={submit}
          aria-label="New subject"
          className="space-y-2 rounded-xl border border-gray-800 bg-gray-950/40 p-3"
        >
          <div className="grid grid-cols-3 gap-2">
            <input
              aria-label="Subject code"
              className={compactInputClass}
              placeholder="PHY-501"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              maxLength={40}
            />
            <input
              aria-label="Subject name"
              className={`${compactInputClass} col-span-2`}
              placeholder="Physics"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={200}
            />
          </div>
          {problem && (
            <p role="alert" className="text-[11px] font-semibold text-red-400">
              {problem}
            </p>
          )}
          <button
            type="submit"
            disabled={create.isPending}
            className="rounded-lg bg-purple-600 px-3 py-1 text-xs font-semibold text-white hover:bg-purple-500 disabled:opacity-50"
          >
            Create subject
          </button>
        </form>
      )}
    </div>
  )
}
