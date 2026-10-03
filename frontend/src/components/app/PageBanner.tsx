import type { ReactNode } from 'react'
import { GlassPanel } from '../ui'

interface Props {
  icon: string
  /** Tailwind text colour of the icon, as the prototype's per-tab accent. */
  iconTone: string
  title: string
  description: string
  children?: ReactNode
}

/** The header banner every tab starts with in project_idea.html. */
export function PageBanner({ icon, iconTone, title, description, children }: Props) {
  return (
    <GlassPanel as="section" className="relative overflow-hidden p-6">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-10 -bottom-10 h-60 w-60 rounded-full bg-purple-600/10 blur-3xl"
      />
      <div className="relative z-10 flex flex-col items-start justify-between gap-4 md:flex-row md:items-center">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
            <i className={`${icon} ${iconTone}`} aria-hidden="true" />
            {title}
          </h1>
          <p className="mt-1 text-sm text-gray-400">{description}</p>
        </div>
        {children}
      </div>
    </GlassPanel>
  )
}
