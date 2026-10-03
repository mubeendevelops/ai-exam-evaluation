import type { ReactNode } from 'react'
import { toneClasses, type Tone } from './tones'

interface Props {
  tone?: Tone
  children: ReactNode
  className?: string
  /** `md` is the size of the "AI Studio" tag in the app header. */
  size?: 'sm' | 'md'
}

/** Small rounded label, e.g. "AI Studio", "Active Portal", a role or a status. */
export function Badge({ tone = 'gray', children, className = '', size = 'sm' }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${size === 'md' ? 'text-xs' : 'text-[10px]'} font-semibold ${toneClasses[tone]} ${className}`}
    >
      {children}
    </span>
  )
}
