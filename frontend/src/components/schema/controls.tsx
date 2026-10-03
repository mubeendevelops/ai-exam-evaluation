import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'

/** Input look of the prototype's designer form (project_idea.html). */
export const inputClass =
  'w-full rounded-xl border border-gray-700 bg-gray-900 px-3 py-2 text-xs text-white placeholder-gray-500 focus:border-purple-500 focus:outline-none'
export const compactInputClass =
  'w-full rounded-lg border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-white placeholder-gray-500 focus:border-purple-500 focus:outline-none'

interface FieldProps {
  label: string
  hint?: ReactNode
  children: (id: string) => ReactNode
}

/** A visible label over a control. */
export function Field({ label, hint, children }: FieldProps) {
  const id = useId()
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-semibold text-gray-300">
        {label}
      </label>
      {children(id)}
      {hint && <p className="mt-1 text-[10px] text-gray-400">{hint}</p>}
    </div>
  )
}

export function TextInput({
  label,
  hint,
  ...input
}: { label: string; hint?: ReactNode } & Omit<InputHTMLAttributes<HTMLInputElement>, 'className'>) {
  return (
    <Field label={label} hint={hint}>
      {(id) => <input id={id} className={inputClass} {...input} />}
    </Field>
  )
}

export function SelectInput({
  label,
  hint,
  children,
  ...select
}: { label: string; hint?: ReactNode } & Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  'className'
>) {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <select id={id} className={inputClass} {...select}>
          {children}
        </select>
      )}
    </Field>
  )
}

/** A small icon button (remove, expand). */
export function IconButton({
  label,
  icon,
  onClick,
  tone = 'text-gray-400 hover:text-white',
  pressed,
}: {
  label: string
  icon: string
  onClick: () => void
  tone?: string
  pressed?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      onClick={onClick}
      className={`rounded-md p-1.5 text-xs ${tone}`}
    >
      <i className={icon} aria-hidden="true" />
    </button>
  )
}

export function SmallButton({
  children,
  onClick,
  icon,
  tone = 'cyan',
  disabled,
}: {
  children: ReactNode
  onClick: () => void
  icon?: string
  tone?: 'cyan' | 'purple' | 'gray'
  disabled?: boolean
}) {
  const tones = {
    cyan: 'border-cyan-500/40 bg-cyan-950 text-cyan-300 hover:bg-cyan-900/50',
    purple: 'border-purple-500/40 bg-purple-950 text-purple-300 hover:bg-purple-900/50',
    gray: 'border-gray-700 bg-gray-800 text-gray-200 hover:bg-gray-700',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs transition disabled:opacity-50 ${tones[tone]}`}
    >
      {icon && <i className={icon} aria-hidden="true" />}
      {children}
    </button>
  )
}
