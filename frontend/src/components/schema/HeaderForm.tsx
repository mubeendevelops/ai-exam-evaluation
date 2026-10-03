import { MARK_STEPS, NEGATIVE_MARKING, calculatedTotal, type FormState } from '../../lib/blueprint'
import { Field, SelectInput, TextInput, inputClass, SmallButton } from './controls'
import { SubjectPicker } from './SubjectPicker'

interface Props {
  form: FormState
  onChange: (patch: Partial<FormState>) => void
}

/** Step 1 of the prototype: the examination header and marks configuration. */
export function HeaderForm({ form, onChange }: Props) {
  const calculated = calculatedTotal(form)
  const auto = form.totalMarks === null
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <TextInput
        label="Exam Title / Header"
        value={form.title}
        placeholder="Physics Mid-Term Assessment 2026"
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
      <SubjectPicker value={form.subjectId} onChange={(subjectId) => onChange({ subjectId })} />
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
    </div>
  )
}
