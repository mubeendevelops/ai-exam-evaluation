import { MARK_STEPS, NEGATIVE_MARKING, calculatedTotal, type FormState } from '../../lib/blueprint'
import { useId } from 'react'
import { Field, SelectInput, TextArea, TextInput, inputClass, SmallButton } from './controls'
import { SubjectPicker } from './SubjectPicker'

interface Props {
  form: FormState
  onChange: (patch: Partial<FormState>) => void
  /** Batches already in use, offered while typing the target batch. */
  batches?: string[]
}

/** Step 1 of the prototype: the examination header and marks configuration. A schema (D219)
 * has a name, a target batch and a weightage description, and no exam code; a paper has an
 * exam code, a descriptor tag and a card description. */
export function HeaderForm({ form, onChange, batches = [] }: Props) {
  const calculated = calculatedTotal(form)
  const auto = form.totalMarks === null
  const schema = form.kind === 'schema'
  const batchList = useId()
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <TextInput
        label={schema ? 'Schema Name' : 'Exam Title / Header'}
        value={form.title}
        placeholder={
          schema ? 'Advanced Quantum Mechanics Matrix' : 'Physics Mid-Term Assessment 2026'
        }
        maxLength={200}
        onChange={(e) => onChange({ title: e.target.value })}
      />
      <TextInput
        label="Course Code"
        value={form.courseCode}
        placeholder="PHY-501"
        maxLength={200}
        onChange={(e) => onChange({ courseCode: e.target.value })}
      />
      {!schema && (
        <TextInput
          label="Exam Code"
          value={form.examCode}
          placeholder="PHY-2026-MIDTERM"
          maxLength={50}
          hint="Names this exam in your college. A paper needs one before the admin can make a unit of it."
          onChange={(e) => onChange({ examCode: e.target.value })}
        />
      )}
      <SubjectPicker value={form.subjectId} onChange={(subjectId) => onChange({ subjectId })} />
      <TextInput
        label="Target Class / Batch"
        value={form.targetBatch}
        placeholder="2024 batch, Class 12…"
        maxLength={100}
        list={batches.length > 0 ? batchList : undefined}
        hint="A filter for your lists only; it is not tied to a unit's year."
        onChange={(e) => onChange({ targetBatch: e.target.value })}
      />
      {batches.length > 0 && (
        <datalist id={batchList}>
          {batches.map((b) => (
            <option key={b} value={b} />
          ))}
        </datalist>
      )}
      {!schema && (
        <TextInput
          label="Descriptor"
          value={form.descriptor}
          placeholder="Mid-term, Quiz, End semester…"
          maxLength={50}
          onChange={(e) => onChange({ descriptor: e.target.value })}
        />
      )}
      <TextInput
        label="Duration (Minutes)"
        inputMode="numeric"
        value={form.duration}
        placeholder="90"
        onChange={(e) => onChange({ duration: e.target.value })}
      />
      <Field
        label="Total Marks"
        hint={
          auto
            ? `Follows the sections: ${calculated}. Type a number to set it yourself.`
            : `The sections add up to ${calculated}.`
        }
      >
        {(id) => (
          <div className="flex gap-2">
            <input
              id={id}
              className={inputClass}
              inputMode="decimal"
              value={form.totalMarks ?? String(calculated)}
              onChange={(e) => onChange({ totalMarks: e.target.value })}
            />
            {!auto && (
              <SmallButton tone="gray" onClick={() => onChange({ totalMarks: null })}>
                Auto
              </SmallButton>
            )}
          </div>
        )}
      </Field>
      <SelectInput
        label="Negative marking (per wrong MCQ)"
        hint="Applies to Exact Pattern Match and OMR Bubble Scan sections."
        value={form.negativeMarking}
        onChange={(e) => onChange({ negativeMarking: e.target.value })}
      >
        {NEGATIVE_MARKING.map((v) => (
          <option key={v} value={v}>
            {v === '0' ? 'None (0)' : `${v} per wrong answer`}
          </option>
        ))}
      </SelectInput>
      <SelectInput
        label="Marks are rounded to"
        hint="The unit an answer's mark is rounded to (half-up)."
        value={form.markStep}
        onChange={(e) => onChange({ markStep: e.target.value })}
      >
        {MARK_STEPS.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </SelectInput>
      <div className="md:col-span-2">
        {schema ? (
          <TextArea
            label="Rubric Weightage Description"
            value={form.weightageNote}
            rows={2}
            maxLength={1000}
            placeholder="Define conceptual vs analytical weightages…"
            hint="Shown on the schema's card; not enforced in scoring."
            onChange={(e) => onChange({ weightageNote: e.target.value })}
          />
        ) : (
          <TextArea
            label="Description"
            value={form.description}
            rows={2}
            maxLength={1000}
            placeholder="What this paper covers"
            onChange={(e) => onChange({ description: e.target.value })}
          />
        )}
      </div>
    </div>
  )
}
