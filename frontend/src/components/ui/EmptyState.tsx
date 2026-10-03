import type { ReactNode } from 'react'

interface Props {
  icon: string
  title: string
  children?: ReactNode
  action?: ReactNode
}

export function EmptyState({ icon, title, children, action }: Props) {
  return (
    <div className="glass-panel flex flex-col items-center rounded-2xl border border-dashed border-gray-700/80 px-6 py-14 text-center">
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-purple-500/30 bg-purple-950/50 text-purple-400">
        <i className={`${icon} text-2xl`} aria-hidden="true" />
      </div>
      <h2 className="text-lg font-semibold text-white">{title}</h2>
      {children && <p className="mt-1 max-w-md text-xs text-gray-400">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
