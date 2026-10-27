import { NavLink, Outlet } from 'react-router'

interface Section {
  to: string
  label: string
  icon: string
  end?: boolean
}

/** The admin mock's sidebar (newrequirements/admin_portal.html), in the mock's order and words;
 * the icons are FontAwesome stand-ins for its Lucide ones. */
export const adminSections: readonly Section[] = [
  { to: '/admin', label: 'Dashboard Overview', icon: 'fa-solid fa-gauge-high', end: true },
  { to: '/admin/smes', label: 'SME Directory & Subjects', icon: 'fa-solid fa-users' },
  { to: '/admin/upload', label: 'Upload & Assign Papers', icon: 'fa-solid fa-file-arrow-up' },
  { to: '/admin/progress', label: 'SME Progress Reports', icon: 'fa-solid fa-chart-line' },
  {
    to: '/admin/redistribute',
    label: 'Redistribute Workload',
    icon: 'fa-solid fa-arrow-right-arrow-left',
  },
  {
    to: '/admin/reports',
    label: 'Audit & External Reports',
    icon: 'fa-solid fa-file-circle-check',
  },
]

const base = 'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium whitespace-nowrap'

/** Sidebar on wide screens, a scrolling row above the page on narrow ones. */
export function AdminLayout() {
  return (
    <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
      <nav
        aria-label="Admin sections"
        className="glass-panel flex gap-1 overflow-x-auto rounded-2xl border border-gray-800/80 p-2 lg:sticky lg:top-24 lg:w-64 lg:shrink-0 lg:flex-col lg:p-3"
      >
        {adminSections.map((section) => (
          <NavLink
            key={section.to}
            to={section.to}
            end={section.end}
            className={({ isActive }) =>
              `${base} ${
                isActive
                  ? 'border border-purple-500/30 bg-purple-950/40 text-purple-300'
                  : 'text-gray-300 hover:bg-gray-800/60 hover:text-white'
              }`
            }
          >
            <i className={`${section.icon} w-5 text-center`} aria-hidden="true" />
            <span>{section.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="min-w-0 grow">
        <Outlet />
      </div>
    </div>
  )
}
