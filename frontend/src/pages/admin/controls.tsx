import type { ButtonHTMLAttributes } from 'react'
import { Modal } from '../../components/ui'

const rowButton =
  'rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700 disabled:opacity-50'

/** A small button in a table row. */
export function RowButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className={rowButton} {...props} />
}

/** The page's main call to action ("Add SME", "Add student"). */
export function PrimaryButton({
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-purple-600 via-indigo-600 to-cyan-500 px-4 py-2.5 text-xs font-bold tracking-wider text-white uppercase shadow-lg shadow-purple-900/40 transition hover:from-purple-500 hover:to-cyan-400 disabled:cursor-not-allowed disabled:opacity-60 ${className}`}
      {...props}
    />
  )
}

export function CancelButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className="rounded-xl bg-gray-800 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-gray-700"
      {...props}
    />
  )
}

/** The submit button of a dialog's form, which lives in the dialog's footer (`form` = form id). */
export function SubmitInDialog({
  form,
  busy,
  disabled,
  children,
}: {
  form: string
  busy?: boolean
  disabled?: boolean
  children: string
}) {
  return (
    <button
      type="submit"
      form={form}
      disabled={busy || disabled}
      className="rounded-xl bg-purple-600 px-4 py-2 text-xs font-bold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {busy ? 'Working…' : children}
    </button>
  )
}

/** Asks before something the admin cannot easily take back. Stands in for `confirm()`. */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  onConfirm,
  onClose,
  children,
}: {
  open: boolean
  title: string
  confirmLabel: string
  onConfirm: () => void
  onClose: () => void
  children: string
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Confirm"
      title={title}
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-xl bg-red-600 px-4 py-2 text-xs font-bold text-white hover:bg-red-500"
          >
            {confirmLabel}
          </button>
        </div>
      }
    >
      <p className="text-sm text-gray-300">{children}</p>
    </Modal>
  )
}

/** The panel heading of the admin pages. */
export const sectionTitle = 'flex items-center gap-2 text-lg font-bold text-white'
