import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { useAuth, useMe } from '../../auth/AuthProvider'

interface Props {
  onProfile: () => void
  onRecoveryCodes: () => void
}

export function initialsOf(name: string): string {
  const parts = name
    .trim()
    .split(/\s+/)
    .filter((part) => /\p{L}/u.test(part))
  const letters = parts.length > 1 ? [parts[0], parts[parts.length - 1]] : [parts[0]]
  const text = letters.map((part) => part?.match(/\p{L}/u)?.[0] ?? '').join('')
  return (text || '?').toUpperCase()
}

const itemClasses =
  'flex w-full items-center gap-2.5 px-4 py-2 text-left text-xs text-gray-300 hover:bg-gray-800/70 hover:text-white'

/** Replaces the prototype's static "TK" avatar: profile, recovery codes, administration, sign out. */
export function UserMenu({ onProfile, onRecoveryCodes }: Props) {
  const me = useMe()
  const { signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const { user } = me
  const choose = (action: () => void) => () => {
    setOpen(false)
    action()
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account menu for ${user.display_name}`}
        className="flex h-8 w-8 items-center justify-center rounded-full border border-purple-500/40 bg-purple-800/50 text-xs font-bold text-purple-200 transition hover:bg-purple-700/60"
      >
        {initialsOf(user.display_name)}
      </button>
      {open && (
        <div
          role="menu"
          className="glass-panel absolute right-0 z-50 mt-2 w-64 overflow-hidden rounded-xl border border-gray-700 py-1 shadow-2xl"
        >
          <div className="border-b border-gray-800 px-4 py-3">
            <p className="truncate text-sm font-semibold text-white">{user.display_name}</p>
            <p className="truncate text-[11px] text-gray-400">{user.email}</p>
            <p className="mt-1 truncate text-[10px] text-gray-500">
              {me.college_name} · <span className="font-mono">{me.institution_id}</span>
            </p>
          </div>
          <button role="menuitem" type="button" className={itemClasses} onClick={choose(onProfile)}>
            <i className="fa-solid fa-user w-4 text-purple-400" aria-hidden="true" /> Profile
          </button>
          <button
            role="menuitem"
            type="button"
            className={itemClasses}
            onClick={choose(onRecoveryCodes)}
          >
            <i className="fa-solid fa-life-ring w-4 text-cyan-400" aria-hidden="true" /> Recovery
            codes
          </button>
          {user.role === 'admin' && (
            <Link
              role="menuitem"
              to="/admin"
              className={itemClasses}
              onClick={() => setOpen(false)}
            >
              <i className="fa-solid fa-user-shield w-4 text-amber-400" aria-hidden="true" />{' '}
              Administration
            </Link>
          )}
          <button
            role="menuitem"
            type="button"
            className={`${itemClasses} border-t border-gray-800`}
            onClick={choose(() => void signOut())}
          >
            <i className="fa-solid fa-right-from-bracket w-4 text-red-400" aria-hidden="true" />{' '}
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
