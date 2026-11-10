import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { NavLink, matchPath, useLocation } from 'react-router'
import { useMe } from '../../auth/AuthProvider'
import { useAssignmentSummary, useMySubjects } from '../../pages/sme/data'
import { AppFooter } from '../app/AppFooter'
import { StatusPill } from '../app/StatusPill'
import { UserMenu, initialsOf } from '../app/UserMenu'

interface Item {
  to: string
  label: string
  icon: string
  end?: boolean
}

interface Group {
  label: string
  items: readonly Item[]
  note?: string
}

/** The SME mock's sidebar (newrequirements/sme_portal.html), in its order and words; the icons
 * are FontAwesome stand-ins for its outline ones (D215). */
export const smeGroups: readonly Group[] = [
  {
    label: 'Workspace',
    items: [
      { to: '/sme', label: 'My Assignments', icon: 'fa-solid fa-clipboard-list', end: true },
      { to: '/sme/papers', label: 'Manage Question Papers', icon: 'fa-solid fa-box-archive' },
      { to: '/sme/schemas', label: 'Manage Schema', icon: 'fa-solid fa-table-cells-large' },
    ],
  },
  {
    label: 'Account & Insights',
    items: [
      { to: '/sme/profile', label: 'My Profile', icon: 'fa-solid fa-user' },
      { to: '/sme/reports', label: 'My Reports', icon: 'fa-solid fa-file-lines' },
    ],
  },
  {
    // Temporary (D215): each goes when the prompt that replaces it is built.
    label: 'Older screens',
    note: 'Until their replacements are built.',
    items: [{ to: '/qna', label: 'Q&A DB', icon: 'fa-solid fa-database' }],
  },
]

/** The header's tab title for each screen the SME frame holds. */
const TITLES: readonly [string, string][] = [
  ['/sme', 'My Assignments'],
  ['/sme/papers', 'Manage Question Papers'],
  ['/sme/papers/:blueprintId', 'Manage Question Papers'],
  ['/sme/schemas', 'Manage Schema'],
  ['/sme/schemas/:blueprintId', 'Manage Schema'],
  ['/sme/profile', 'My Profile'],
  ['/sme/reports', 'My Reports'],
  ['/qna', 'Q&A DB'],
  ['/sme/units/:unitId', 'My Assignments'],
  ['/sme/units/:unitId/booklets/:bookletId', 'My Assignments'],
  ['/evaluate', 'Evaluation'],
]

export function titleOf(pathname: string): string {
  return TITLES.find(([path]) => matchPath(path, pathname))?.[1] ?? 'My Assignments'
}

const SOON = 'Arrives with notifications and global search in a later update.'

const link =
  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-purple-400'

/**
 * The SME portal frame (P31, D215): a sidebar with the mock's groups and the SME's name and
 * first subject at its foot, and a header with the tab title, search, bell and the user menu.
 * Below 768 px the sidebar is a drawer opened by the menu button: focus moves into it, Tab
 * stays inside, Escape or the backdrop closes it and focus returns to the button.
 */
export function SmeLayout({
  onProfile,
  onRecoveryCodes,
  children,
}: {
  onProfile: () => void
  onRecoveryCodes: () => void
  children: ReactNode
}) {
  const me = useMe()
  const location = useLocation()
  const summary = useAssignmentSummary()
  const subjects = useMySubjects()
  // The drawer is open for the page it was opened on: following a link (or Back) closes it.
  const [openOn, setOpenOn] = useState<string | null>(null)
  const open = openOn === location.key
  const nav = useRef<HTMLElement>(null)
  const menuButton = useRef<HTMLButtonElement>(null)
  const navId = useId()

  useEffect(() => {
    if (open) nav.current?.querySelector<HTMLElement>('a')?.focus()
  }, [open])

  function close() {
    setOpenOn(null)
    menuButton.current?.focus()
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!open) return
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
      return
    }
    if (event.key !== 'Tab' || !nav.current) return
    const stops = [...nav.current.querySelectorAll<HTMLElement>('a, button')]
    const first = stops[0]
    const last = stops[stops.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }

  const firstSubject = subjects.data?.[0]?.name
  const active = summary.data?.active ?? 0

  return (
    <div className="flex min-h-screen">
      {open && (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={close}
        />
      )}
      <aside
        ref={nav}
        id={navId}
        onKeyDown={onKeyDown}
        className={`${
          open ? 'fixed inset-y-0 left-0 z-50 flex' : 'hidden'
        } w-64 shrink-0 flex-col justify-between border-r border-gray-800 bg-gray-950/95 md:sticky md:top-0 md:flex md:h-screen`}
      >
        <div className="overflow-y-auto">
          <div className="flex items-center gap-3 border-b border-gray-800 px-5 py-4">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-tr from-purple-600 to-cyan-400 text-white">
              <i className="fa-solid fa-brain" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-extrabold tracking-tight text-white">Tarn Knowledge</p>
              <p className="text-[10px] text-gray-400">SME Operations Portal</p>
            </div>
          </div>
          <nav aria-label="SME sections" className="space-y-5 p-4">
            {smeGroups.map((group) => (
              <div key={group.label} role="group" aria-label={group.label}>
                <p className="mb-1.5 px-3 text-[10px] font-semibold tracking-wider text-gray-500 uppercase">
                  {group.label}
                </p>
                {group.note && (
                  <p className="mb-1.5 px-3 text-[10px] text-gray-500">{group.note}</p>
                )}
                <ul className="space-y-1">
                  {group.items.map((item) => (
                    <li key={item.to}>
                      <NavLink
                        to={item.to}
                        end={item.end}
                        className={({ isActive }) =>
                          `${link} ${
                            isActive
                              ? 'border border-purple-500/30 bg-purple-950/40 text-purple-300'
                              : 'text-gray-300 hover:bg-gray-800/60 hover:text-white'
                          }`
                        }
                      >
                        <i className={`${item.icon} w-5 text-center`} aria-hidden="true" />
                        <span>{item.label}</span>
                        {item.to === '/sme' && active > 0 && (
                          <span className="ml-auto rounded-full bg-cyan-950/80 px-2 py-0.5 text-[10px] text-cyan-300">
                            {active} Active
                          </span>
                        )}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3 border-t border-gray-800 p-4">
          <div
            aria-hidden="true"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-purple-800/50 text-xs font-bold text-purple-200"
          >
            {initialsOf(me.user.display_name)}
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-white">{me.user.display_name}</p>
            <p className="truncate text-[11px] text-gray-400">
              {firstSubject ? `${firstSubject} SME` : 'SME'}
            </p>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 grow flex-col">
        <header className="glass-panel sticky top-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-gray-800/80 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button
              ref={menuButton}
              type="button"
              aria-label="Menu"
              aria-expanded={open}
              aria-controls={navId}
              onClick={() => setOpenOn(open ? null : location.key)}
              className="rounded-lg p-2 text-gray-300 hover:bg-gray-800 md:hidden"
            >
              <i className="fa-solid fa-bars" aria-hidden="true" />
            </button>
            <p className="truncate text-lg font-semibold text-white">
              {titleOf(location.pathname)}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden xl:block">
              <StatusPill />
            </div>
            <input
              type="search"
              disabled
              aria-label="Search assignments, schemas"
              placeholder="Search assignments, schemas..."
              title={SOON}
              className="hidden w-56 rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-1.5 text-xs text-gray-400 disabled:cursor-not-allowed sm:block lg:w-72"
            />
            <button
              type="button"
              disabled
              aria-label="Notifications"
              title={SOON}
              className="rounded-lg p-2 text-gray-500 disabled:cursor-not-allowed"
            >
              <i className="fa-solid fa-bell" aria-hidden="true" />
            </button>
            <UserMenu onProfile={onProfile} onRecoveryCodes={onRecoveryCodes} />
          </div>
        </header>
        <main className="w-full grow px-4 py-6 sm:px-6 lg:px-8">{children}</main>
        <AppFooter />
      </div>
    </div>
  )
}
