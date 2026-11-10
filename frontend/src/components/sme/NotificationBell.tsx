import { useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  sentenceOf,
  targetOf,
  useMarkRead,
  useNotifications,
  useUnreadCount,
  type Notice,
} from './notifications'

const ICONS: Record<Notice['kind'], string> = {
  assigned: 'fa-solid fa-clipboard-list text-purple-300',
  transferred_away: 'fa-solid fa-right-left text-gray-400',
  returned: 'fa-solid fa-rotate-left text-amber-300',
  request_done: 'fa-solid fa-circle-check text-emerald-300',
  request_declined: 'fa-solid fa-circle-xmark text-red-300',
  due_soon: 'fa-solid fa-hourglass-half text-amber-300',
  overdue: 'fa-solid fa-triangle-exclamation text-red-300',
}

function when(iso: string, now: number): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return days === 1 ? 'yesterday' : `${days} days ago`
}

/** The SME header's bell (P37, D227): the unread count, polled every minute while the tab is
 * shown, and a panel of the latest notices; each opens its unit and is marked read. */
export function NotificationBell() {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const navigate = useNavigate()
  const unread = useUnreadCount()
  const list = useNotifications(open)
  const mark = useMarkRead()

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        button.current?.focus()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const count = unread.data ?? 0
  const label = count > 0 ? `Notifications, ${count} unread` : 'Notifications'
  // Times are told against when the list was read (rendering stays pure).
  const now = list.dataUpdatedAt
  const today = new Date(now).toISOString().slice(0, 10)

  const choose = (notice: Notice) => {
    if (!notice.read_at) mark.mutate(notice.id)
    setOpen(false)
    void navigate(targetOf(notice))
  }

  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
        className="relative rounded-lg p-2 text-gray-300 hover:bg-gray-800 focus-visible:outline-2 focus-visible:outline-purple-400"
      >
        <i className="fa-solid fa-bell" aria-hidden="true" />
        {count > 0 && (
          <span
            aria-hidden="true"
            className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-red-500 px-1 text-center text-[10px] leading-4 font-bold text-white"
          >
            {count > 9 ? '9+' : count}
          </span>
        )}
      </button>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label="Notifications"
          className="glass-panel absolute right-0 z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-gray-700 shadow-2xl"
        >
          <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2.5">
            <p className="text-sm font-semibold text-white">Notifications</p>
            <button
              type="button"
              onClick={() => mark.mutate('all')}
              disabled={count === 0 || mark.isPending}
              className="text-xs text-purple-300 hover:text-purple-200 disabled:cursor-not-allowed disabled:text-gray-600"
            >
              Mark all read
            </button>
          </div>
          {list.isPending ? (
            <p className="px-4 py-6 text-center text-xs text-gray-400">Loading…</p>
          ) : list.isError ? (
            <p role="alert" className="px-4 py-6 text-center text-xs text-red-300">
              Notifications could not be loaded.
            </p>
          ) : list.data.items.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-gray-400">No notifications yet.</p>
          ) : (
            <ul aria-label="Latest notifications" className="max-h-96 overflow-y-auto">
              {list.data.items.map((notice) => (
                <li key={notice.id} className="border-b border-gray-800/70 last:border-b-0">
                  <button
                    type="button"
                    onClick={() => choose(notice)}
                    className={`flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-gray-800/60 ${notice.read_at ? '' : 'bg-purple-900/10'}`}
                  >
                    <i className={`${ICONS[notice.kind]} mt-0.5 w-4`} aria-hidden="true" />
                    <span className="min-w-0 grow">
                      <span
                        className={`block text-xs ${notice.read_at ? 'text-gray-400' : 'text-gray-100'}`}
                      >
                        {sentenceOf(notice, today)}
                      </span>
                      <span className="mt-0.5 block text-[10px] text-gray-500">
                        {when(notice.created_at, now)}
                        {notice.read_at ? '' : ' · Unread'}
                      </span>
                    </span>
                    {!notice.read_at && (
                      <span
                        aria-hidden="true"
                        className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-purple-400"
                      />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {list.data && list.data.total > list.data.items.length && (
            <p className="border-t border-gray-800 px-4 py-2 text-center text-[10px] text-gray-500">
              The latest {list.data.items.length} of {list.data.total}.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
