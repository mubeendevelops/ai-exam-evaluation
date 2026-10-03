interface Props {
  steps: readonly string[]
  /** Zero-based index of the current step. */
  current: number
  onSelect?: (index: number) => void
  label?: string
}

/** "1. Scan Upload › 2. AI Segmentation › 3. Evaluation View" from the evaluation tab. */
export function Stepper({ steps, current, onSelect, label = 'Progress' }: Props) {
  return (
    <ol
      aria-label={label}
      className="flex max-w-full shrink-0 items-center space-x-2 overflow-x-auto rounded-xl border border-gray-800 bg-gray-900/80 p-1.5 text-xs"
    >
      {steps.map((step, index) => {
        const active = index === current
        const classes = active
          ? 'border border-cyan-500/40 bg-cyan-950/80 text-cyan-300 font-medium'
          : 'text-gray-400 hover:text-white'
        return (
          <li key={step} className="flex items-center space-x-2">
            {index > 0 && (
              <i
                className="fa-solid fa-chevron-right text-[10px] text-gray-600"
                aria-hidden="true"
              />
            )}
            <button
              type="button"
              aria-current={active ? 'step' : undefined}
              disabled={!onSelect}
              onClick={() => onSelect?.(index)}
              className={`rounded-lg px-3 py-1.5 whitespace-nowrap ${classes} disabled:cursor-default`}
            >
              {index + 1}. {step}
            </button>
          </li>
        )
      })}
    </ol>
  )
}
