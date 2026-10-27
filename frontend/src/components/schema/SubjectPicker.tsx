import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Field, inputClass } from './controls'

interface Props {
  value: string
  onChange: (subjectId: string) => void
}

/**
 * The subject of the paper: one of the subjects the admin granted me (D160). SMEs no longer
 * create subjects; the admin keeps the college's list.
 */
export function SubjectPicker({ value, onChange }: Props) {
  const subjects = useQuery({
    queryKey: ['subjects'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/subjects')
      if (!data) throw new Error('subjects unavailable')
      return data
    },
  })

  return (
    <Field
      label="Subject"
      hint={
        subjects.isError ? (
          <span className="text-red-400">Could not load the subjects. Reload to try again.</span>
        ) : subjects.data?.length === 0 ? (
          'You have no subjects yet: ask your admin to grant you one.'
        ) : undefined
      }
    >
      {(id) => (
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
      )}
    </Field>
  )
}
