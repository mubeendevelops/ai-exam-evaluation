import { useState } from 'react'
import { Outlet } from 'react-router'
import { useMe } from '../../auth/AuthProvider'
import { can, type Capability } from '../../auth/capabilities'
import { brand } from '../../content/landing'
import { Badge, TabNav, ToastProvider, type TabItem } from '../ui'
import { AppFooter } from './AppFooter'
import { ProfileModal } from './ProfileModal'
import { RecoveryCodesModal } from './RecoveryCodesModal'
import { StatusPill } from './StatusPill'
import { UserMenu } from './UserMenu'

/** Tab names follow the build plan (D51); the icons are the prototype's. Evaluated (P18) is the
 * evaluated booklets list the build plan asks for: the prototype has no screen for it (D128). */
export const appTabs: readonly (TabItem & { capability: Capability })[] = [
  { to: '/qna', label: 'Q&A DB', icon: 'fa-solid fa-database', capability: 'author_content' },
  {
    to: '/evaluate',
    label: 'AI Evaluation',
    shortLabel: 'AI Eval',
    icon: 'fa-solid fa-microchip',
    capability: 'evaluate',
  },
  { to: '/evaluated', label: 'Evaluated', icon: 'fa-solid fa-box-archive', capability: 'evaluate' },
  {
    to: '/schema',
    label: 'Schema Designer',
    shortLabel: 'Schema',
    icon: 'fa-solid fa-file-signature',
    capability: 'author_content',
  },
]

/** The signed-in frame of project_idea.html: top bar with the tabs, mobile sub-bar, footer. */
export function AppShell() {
  const [dialog, setDialog] = useState<'profile' | 'codes' | null>(null)
  const me = useMe()
  // The SME's tabs; the admin's screens come in P25 and the Governor's later (D159).
  const tabs = appTabs.filter((tab) => can(me, tab.capability))
  return (
    <ToastProvider>
      <div className="theme-app flex min-h-screen flex-col justify-between selection:bg-purple-500 selection:text-white">
        <header className="glass-panel sticky top-0 z-40 border-b border-gray-800/80">
          <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="flex h-16 items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-tr from-purple-600 via-indigo-600 to-cyan-400 text-xl font-bold text-white shadow-lg shadow-purple-900/30">
                  <i className="fa-solid fa-brain" aria-hidden="true" />
                </div>
                <div>
                  <a
                    href={brand.site}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-2"
                  >
                    <span className="bg-gradient-to-r from-white via-gray-200 to-purple-400 bg-clip-text text-lg whitespace-nowrap sm:text-xl font-extrabold tracking-tight text-transparent transition-all duration-300 group-hover:to-cyan-400">
                      {brand.name}
                    </span>
                    <Badge
                      tone="purple"
                      size="md"
                      className="hidden tracking-wider uppercase sm:inline-flex"
                    >
                      AI Studio
                    </Badge>
                  </a>
                  <p className="-mt-1 hidden text-[10px] font-medium tracking-wider text-gray-400 uppercase sm:block">
                    Services &amp; Assessment Platform
                  </p>
                </div>
              </div>

              {tabs.length > 0 && <TabNav items={tabs} variant="desktop" />}

              <div className="flex items-center space-x-4">
                <StatusPill />
                <UserMenu
                  onProfile={() => setDialog('profile')}
                  onRecoveryCodes={() => setDialog('codes')}
                />
              </div>
            </div>
          </div>
          {tabs.length > 0 && <TabNav items={tabs} variant="mobile" />}
        </header>

        <main className="mx-auto w-full max-w-7xl grow px-4 py-6 sm:px-6 lg:px-8">
          <Outlet />
        </main>

        <AppFooter />
        <ProfileModal open={dialog === 'profile'} onClose={() => setDialog(null)} />
        <RecoveryCodesModal open={dialog === 'codes'} onClose={() => setDialog(null)} />
      </div>
    </ToastProvider>
  )
}
