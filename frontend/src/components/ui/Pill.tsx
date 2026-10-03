import type { ReactNode } from 'react'
import { dotClasses, toneClasses, type Tone } from './tones'

interface Props {
  tone?: Tone
  /** Pulsing dot, as the prototype's status pill. */
  pulse?: boolean
  title?: string
  className?: string
  children: ReactNode
}

/** Status pill with a coloured dot (the prototype's "AI Generator Ready"). */
export function Pill({ tone = 'emerald', pulse = false, title, className = '', children }: Props) {
  return (
    <div
      role="status"
      title={title}
      className={`flex items-center space-x-2 rounded-full border px-3 py-1 text-xs ${toneClasses[tone]} ${className}`}
    >
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full ${dotClasses[tone]} ${pulse ? 'animate-pulse' : ''}`}
      />
      <span className="font-medium">{children}</span>
    </div>
  )
}
