import { useEffect, useEffectEvent, useId, useRef, type ReactNode } from 'react'

interface Props {
  open: boolean
  onClose: () => void
  title: string
  /** Small label above the title, as "Tenant Registration" on the reference. */
  eyebrow?: string
  /** One line under the title. */
  description?: string
  children: ReactNode
  footer?: ReactNode
  /** Tailwind max-width class of the panel. */
  size?: 'max-w-xl' | 'max-w-3xl' | 'max-w-5xl'
  /** Public pages use `glass-box`, the app `glass-panel`. */
  variant?: 'public' | 'app'
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Dialog on a dimmed, blurred backdrop. Escape and a click on the backdrop close it, Tab stays
 * inside, focus moves in on open and returns to the opener on close. Render it at the page root,
 * not inside an element with `backdrop-filter` (that would trap `position: fixed`).
 */
export function Modal({
  open,
  onClose,
  title,
  eyebrow,
  description,
  children,
  footer,
  size = 'max-w-xl',
  variant = 'app',
}: Props) {
  const titleId = useId()
  const panel = useRef<HTMLDivElement>(null)
  const requestClose = useEffectEvent(onClose)

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    panel.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        requestClose()
        return
      }
      if (event.key !== 'Tab' || !panel.current) return
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
      const first = items[0]
      const last = items[items.length - 1]
      if (!first || !last) return
      if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === panel.current)
      ) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      opener?.focus?.()
    }
  }, [open])

  if (!open) return null
  const glass = variant === 'public' ? 'glass-box' : 'glass-panel'
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/80 p-3 backdrop-blur-md sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`${glass} relative my-auto flex max-h-[92vh] w-full ${size} flex-col rounded-3xl border border-purple-500/30 p-6 shadow-2xl outline-none sm:p-8`}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute top-5 right-5 p-1 text-xl text-gray-400 hover:text-white"
        >
          <i className="fa-solid fa-xmark" aria-hidden="true" />
        </button>
        <div className="mb-4 shrink-0 pr-8">
          {eyebrow && (
            <span className="rounded-full border border-purple-500/30 bg-purple-950/80 px-2.5 py-1 text-[10px] font-bold tracking-widest text-purple-400 uppercase">
              {eyebrow}
            </span>
          )}
          <h2 id={titleId} className="mt-2 text-2xl font-black text-white">
            {title}
          </h2>
          {description && <p className="mt-1 text-xs text-gray-400">{description}</p>}
        </div>
        <div className="custom-scrollbar grow overflow-y-auto">{children}</div>
        {footer && <div className="mt-4 shrink-0 border-t border-gray-800 pt-4">{footer}</div>}
      </div>
    </div>
  )
}
