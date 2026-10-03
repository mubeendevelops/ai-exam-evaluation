export type Tone = 'purple' | 'cyan' | 'emerald' | 'amber' | 'red' | 'indigo' | 'gray'

/** Classes of the prototypes' pills and badges: tinted background, border and text. */
export const toneClasses: Record<Tone, string> = {
  purple: 'bg-purple-950/80 border-purple-500/40 text-purple-300',
  cyan: 'bg-cyan-950/80 border-cyan-500/30 text-cyan-300',
  emerald: 'bg-emerald-950/60 border-emerald-500/30 text-emerald-400',
  amber: 'bg-amber-950/60 border-amber-500/30 text-amber-300',
  red: 'bg-red-950/60 border-red-500/30 text-red-400',
  indigo: 'bg-indigo-950/70 border-indigo-500/30 text-indigo-300',
  gray: 'bg-gray-800 border-gray-700 text-gray-300',
}

export const dotClasses: Record<Tone, string> = {
  purple: 'bg-purple-400',
  cyan: 'bg-cyan-400',
  emerald: 'bg-emerald-400',
  amber: 'bg-amber-400',
  red: 'bg-red-400',
  indigo: 'bg-indigo-400',
  gray: 'bg-gray-500',
}
