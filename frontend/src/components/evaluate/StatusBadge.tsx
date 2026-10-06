import { Badge } from '../ui'
import { STATUS, type BookletStatus } from './model'

const DOT: Record<string, string> = {
  gray: 'bg-gray-500',
  cyan: 'bg-cyan-400',
  purple: 'bg-purple-400',
  emerald: 'bg-emerald-400',
  indigo: 'bg-indigo-400',
  amber: 'bg-amber-400',
  red: 'bg-red-400',
}

/** A booklet's status as a pill; a pulsing dot while the machine is still working on it. */
export function StatusBadge({ status }: { status: BookletStatus }) {
  const meta = STATUS[status]
  return (
    <Badge tone={meta.tone} size="md">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${DOT[meta.tone]} ${meta.working ? 'animate-pulse' : ''}`}
      />
      {meta.label}
    </Badge>
  )
}
