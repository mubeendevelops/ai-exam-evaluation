import { useId, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'

const ACCEPT = '.pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png'

/** The prototype's dashed drop area: drag files onto it, or press it to choose them. */
export function Dropzone({
  disabled,
  hint,
  onFiles,
  accept = ACCEPT,
  title = 'Drag & Drop Scanned Student Answer Sheets',
  subtitle = 'Supports a PDF, or JPEG and PNG page scans in page order. One booklet per drop.',
  label = 'Drag and drop scanned student answer sheets, or press to choose files',
}: {
  disabled: boolean
  /** Why it is off, shown under the title. */
  hint?: string
  onFiles: (files: File[]) => void
  accept?: string
  title?: string
  subtitle?: string
  label?: string
}) {
  const input = useRef<HTMLInputElement>(null)
  const inputId = useId()
  const [over, setOver] = useState(false)

  const take = (list: FileList | null) => {
    if (!list || list.length === 0) return
    onFiles([...list])
    if (input.current) input.current.value = ''
  }
  const drop = (event: DragEvent) => {
    event.preventDefault()
    setOver(false)
    if (!disabled) take(event.dataTransfer.files)
  }
  const key = (event: KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (!disabled) input.current?.click()
    }
  }

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      aria-label={label}
      onClick={() => !disabled && input.current?.click()}
      onKeyDown={key}
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
      className={`group glass-panel rounded-2xl border-2 border-dashed p-8 text-center transition ${
        disabled
          ? 'cursor-not-allowed border-gray-800 opacity-60'
          : over
            ? 'cursor-pointer border-purple-500 bg-purple-950/30'
            : 'cursor-pointer border-gray-700/80 hover:border-purple-500'
      }`}
    >
      <input
        id={inputId}
        ref={input}
        type="file"
        multiple
        accept={accept}
        className="hidden"
        aria-label="Answer sheet files"
        tabIndex={-1}
        onChange={(e) => take(e.target.files)}
      />
      <div
        className={`mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-purple-500/30 bg-purple-950/50 text-purple-400 transition duration-300 ${disabled ? '' : 'group-hover:scale-110'}`}
      >
        <i className="fa-solid fa-cloud-arrow-up text-2xl" aria-hidden="true" />
      </div>
      <h3 className="text-lg font-semibold text-white">{title}</h3>
      <p className="mt-1 text-xs text-gray-400">{subtitle}</p>
      {hint && <p className="mt-2 text-xs font-semibold text-amber-300">{hint}</p>}
    </div>
  )
}
