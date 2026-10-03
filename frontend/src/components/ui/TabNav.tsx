import { NavLink } from 'react-router'

export interface TabItem {
  to: string
  label: string
  /** Shown on the mobile sub-bar; defaults to `label`. */
  shortLabel?: string
  icon: string
}

interface Props {
  items: readonly TabItem[]
  variant: 'desktop' | 'mobile'
}

const desktopBase =
  'px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 flex items-center gap-2'
const mobileBase =
  'px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 whitespace-nowrap'

/** The three-tab navigation of the prototype: top bar on desktop, sub-bar on phones. */
export function TabNav({ items, variant }: Props) {
  const desktop = variant === 'desktop'
  return (
    <nav
      aria-label={desktop ? 'Main' : 'Main (mobile)'}
      className={
        desktop
          ? 'hidden space-x-1 md:flex lg:space-x-2'
          : 'flex justify-around overflow-x-auto border-t border-gray-800 bg-gray-900/90 px-2 py-2 md:hidden'
      }
    >
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            [
              desktop ? desktopBase : mobileBase,
              isActive
                ? desktop
                  ? 'border border-purple-500/30 bg-purple-950/40 text-purple-400'
                  : 'bg-purple-950/50 text-purple-400'
                : desktop
                  ? 'text-gray-400 hover:bg-gray-800/50 hover:text-white'
                  : 'text-gray-400',
            ].join(' ')
          }
        >
          <i className={`${item.icon} ${desktop ? 'text-xs' : ''}`} aria-hidden="true" />
          <span>{desktop ? item.label : (item.shortLabel ?? item.label)}</span>
        </NavLink>
      ))}
    </nav>
  )
}
