import { useId, type InputHTMLAttributes, type ReactNode } from 'react'

const inputClasses =
  'w-full rounded-xl border border-gray-700/80 bg-gray-950/80 py-2.5 pr-3 text-xs text-white placeholder-gray-500 transition focus:border-purple-500 focus:outline-none'

interface Props extends Omit<InputHTMLAttributes<HTMLInputElement>, 'className'> {
  label: string
  /** FontAwesome classes of the icon at the left of the input. */
  icon?: string
  hint?: ReactNode
  /** A button or note at the right edge of the input (the "Check" button). */
  trailing?: ReactNode
  error?: string | null
  inputClassName?: string
  /** The red asterisk beside the label (MainLogin.html marks only the Institution ID). */
  requiredMark?: boolean
}

/** Labelled input in the look of MainLogin.html. */
export function TextField({
  label,
  icon,
  hint,
  trailing,
  error,
  id,
  inputClassName = '',
  requiredMark = false,
  ...input
}: Props) {
  const generated = useId()
  const fieldId = id ?? generated
  const hintId = `${fieldId}-hint`
  const errorId = `${fieldId}-error`
  return (
    <div>
      <div className="mb-1 flex items-baseline gap-1">
        <label
          htmlFor={fieldId}
          className="text-xs font-bold tracking-wider text-gray-300 uppercase"
        >
          {label}
        </label>
        {requiredMark && (
          <span aria-hidden="true" className="text-xs text-red-400">
            *
          </span>
        )}
      </div>
      <div className="relative">
        {icon && (
          <i
            className={`${icon} absolute top-3 left-3.5 text-xs text-gray-500`}
            aria-hidden="true"
          />
        )}
        <input
          id={fieldId}
          aria-describedby={
            [hint ? hintId : '', error ? errorId : ''].filter(Boolean).join(' ') || undefined
          }
          aria-invalid={error ? true : undefined}
          className={`${inputClasses} ${icon ? 'pl-9' : 'pl-3'} ${inputClassName}`}
          {...input}
        />
        {trailing && <div className="absolute top-1.5 right-2">{trailing}</div>}
      </div>
      {hint && (
        <p id={hintId} className="mt-1 text-[10px] text-gray-400">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="mt-1 text-[11px] font-semibold text-red-400">
          {error}
        </p>
      )}
    </div>
  )
}

/** The wide gradient submit button of the sign-in and registration cards. */
export function SubmitButton({
  children,
  busy,
  disabled,
}: {
  children: ReactNode
  busy?: boolean
  disabled?: boolean
}) {
  return (
    <button
      type="submit"
      disabled={busy || disabled}
      className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-purple-600 via-indigo-600 to-cyan-500 py-3 text-xs font-bold tracking-wider text-white uppercase shadow-lg shadow-purple-900/40 transition hover:from-purple-500 hover:to-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {busy ? <i className="fa-solid fa-circle-notch fa-spin" aria-hidden="true" /> : children}
    </button>
  )
}

/** Error box under a form: one message and, for password policy failures, the reasons. */
export function FormError({
  message,
  reasons = [],
}: {
  message: string | null
  reasons?: string[]
}) {
  if (!message) return null
  return (
    <div
      role="alert"
      className="rounded-xl border border-red-500/30 bg-red-950/40 p-3 text-xs text-red-300"
    >
      <p className="font-semibold">{message}</p>
      {reasons.length > 0 && (
        <ul className="mt-1 list-inside list-disc space-y-0.5 text-red-300/90">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
