import type { ReactNode } from 'react'
import { Link } from 'react-router'

interface Props {
  icon: string
  title: string
  subtitle?: string
  children: ReactNode
}

/** The card the recovery and verification screens share, in the look of the sign-in card. */
export function AuthScreen({ icon, title, subtitle, children }: Props) {
  return (
    <div className="mx-auto max-w-md py-6">
      <div className="glass-box relative overflow-hidden rounded-3xl border border-purple-500/30 p-8 shadow-2xl">
        <div
          aria-hidden="true"
          className="absolute -top-12 -right-12 h-32 w-32 rounded-full bg-purple-500/20 blur-2xl"
        />
        <div className="mb-6">
          <h1 className="flex items-center gap-2 text-xl font-bold text-white">
            <i className={`${icon} text-purple-400`} aria-hidden="true" /> {title}
          </h1>
          {subtitle && <p className="mt-1 text-xs text-gray-400">{subtitle}</p>}
        </div>
        {children}
      </div>
    </div>
  )
}

export function BackToSignIn({ label = 'Back to sign in' }: { label?: string }) {
  return (
    <Link
      to="/"
      className="mt-5 inline-flex items-center gap-2 text-xs font-semibold text-purple-400 hover:text-purple-300"
    >
      <i className="fa-solid fa-arrow-left" aria-hidden="true" /> {label}
    </Link>
  )
}

export function Notice({
  tone = 'emerald',
  icon,
  children,
}: {
  tone?: 'emerald' | 'amber' | 'red'
  icon: string
  children: ReactNode
}) {
  const classes = {
    emerald: 'border-emerald-500/30 bg-emerald-950/30 text-emerald-200',
    amber: 'border-amber-500/30 bg-amber-950/30 text-amber-200',
    red: 'border-red-500/30 bg-red-950/40 text-red-300',
  }[tone]
  return (
    <div className={`flex items-start gap-3 rounded-xl border p-4 text-xs ${classes}`}>
      <i className={`${icon} mt-0.5 text-base`} aria-hidden="true" />
      <div className="space-y-1.5">{children}</div>
    </div>
  )
}
