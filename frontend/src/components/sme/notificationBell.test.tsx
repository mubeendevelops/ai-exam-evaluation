import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderApp } from '../../test/render'
import { noticeOf, smeSession } from '../../test/smeFixtures'
import { sentenceOf } from './notifications'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
})

const NOTICES = [
  noticeOf({
    id: 'n-3',
    kind: 'returned',
    unit_id: 'unit-2',
    unit_code: 'U-0002',
    note: 'Q3 again',
  }),
  noticeOf({ id: 'n-2', kind: 'transferred_away', unit_id: 'unit-9', unit_code: 'U-0009' }),
  noticeOf({ id: 'n-1', kind: 'due_soon', read_at: '2026-11-10T09:00:00Z' }),
]

function withNotices(unread = 2) {
  let count = unread
  return smeSession({
    'GET /api/v1/notifications/unread-count': () => ({ json: { unread: count } }),
    'GET /api/v1/notifications': {
      json: { items: NOTICES, total: 3, unread: count, limit: 20, offset: 0 },
    },
    'POST /api/v1/notifications/n-3/read': () => {
      count -= 1
      return { json: { ...NOTICES[0], read_at: '2026-11-10T10:00:00Z' } }
    },
    'POST /api/v1/notifications/n-2/read': () => {
      count -= 1
      return { json: { ...NOTICES[1], read_at: '2026-11-10T10:00:00Z' } }
    },
    'POST /api/v1/notifications/read-all': () => {
      const marked = count
      count = 0
      return { json: { marked } }
    },
  })
}

function setVisible(visible: boolean) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: visible ? 'visible' : 'hidden',
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('the notification bell (P37, D227)', () => {
  it('shows the unread count and lists the notices in a panel', async () => {
    withNotices()
    const user = userEvent.setup()
    renderApp('/sme')
    const bell = await screen.findByRole('button', { name: 'Notifications, 2 unread' })
    expect(bell).toHaveAttribute('aria-expanded', 'false')
    await user.click(bell)
    const panel = await screen.findByRole('dialog', { name: 'Notifications' })
    const items = await within(panel).findAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Your admin returned unit U-0002: “Q3 again”')
    expect(items[0]).toHaveTextContent('Unread')
    expect(items[1]).toHaveTextContent('Unit U-0009 was moved to another SME')
    expect(items[2]).not.toHaveTextContent('Unread')
    // Escape closes the panel and gives focus back to the bell.
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Notifications' })).toBeNull()
    expect(bell).toHaveFocus()
  })

  it('opens the unit of a notice and marks it read', async () => {
    const api = withNotices()
    const user = userEvent.setup()
    renderApp('/sme')
    await user.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }))
    const panel = await screen.findByRole('dialog', { name: 'Notifications' })
    await user.click(await within(panel).findByText(/returned unit U-0002/))
    await waitFor(() => expect(api.callsTo('POST /api/v1/notifications/n-3/read')).toHaveLength(1))
    // The student list of the unit asks for it.
    await waitFor(() => expect(api.callsTo('GET /api/v1/units/unit-2').length).toBeGreaterThan(0))
    expect(await screen.findByRole('button', { name: 'Notifications, 1 unread' })).toBeVisible()
  })

  it('sends a notice about a unit that moved away to My Assignments', async () => {
    const api = withNotices()
    const user = userEvent.setup()
    renderApp('/sme/profile')
    await user.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }))
    await user.click(await screen.findByText(/U-0009 was moved/))
    await waitFor(() => expect(api.callsTo('POST /api/v1/notifications/n-2/read')).toHaveLength(1))
    expect(
      await screen.findByRole('heading', { name: 'My Assignments', level: 1 }),
    ).toBeInTheDocument()
  })

  it('marks every notice read', async () => {
    const api = withNotices()
    const user = userEvent.setup()
    renderApp('/sme')
    await user.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }))
    await user.click(await screen.findByRole('button', { name: 'Mark all read' }))
    await waitFor(() => expect(api.callsTo('POST /api/v1/notifications/read-all')).toHaveLength(1))
    expect(await screen.findByRole('button', { name: 'Notifications' })).toBeVisible()
  })

  it('says so when there is nothing', async () => {
    smeSession()
    const user = userEvent.setup()
    renderApp('/sme')
    await user.click(await screen.findByRole('button', { name: 'Notifications' }))
    expect(await screen.findByText('No notifications yet.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Mark all read' })).toBeDisabled()
  })

  it('caps the badge at 9+', async () => {
    withNotices(12)
    renderApp('/sme')
    const bell = await screen.findByRole('button', { name: 'Notifications, 12 unread' })
    expect(bell).toHaveTextContent('9+')
  })

  it('polls every minute while the tab is shown and stops while it is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const api = withNotices()
    renderApp('/sme')
    await screen.findByRole('button', { name: 'Notifications, 2 unread' })
    const count = () => api.callsTo('GET /api/v1/notifications/unread-count').length
    const first = count()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    await waitFor(() => expect(count()).toBe(first + 1))
    act(() => setVisible(false))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000)
    })
    expect(count()).toBe(first + 1)
    act(() => setVisible(true))
    await waitFor(() => expect(count()).toBe(first + 2))
  })
})

describe('notice sentences', () => {
  it('words each kind with codes and dates only', () => {
    const today = '2026-11-10'
    expect(sentenceOf(noticeOf({ kind: 'due_soon', due_date: '2026-11-13' }), today)).toBe(
      'Unit U-0001 is due in 3 days (2026-11-13).',
    )
    expect(sentenceOf(noticeOf({ kind: 'due_soon', due_date: '2026-11-10' }), today)).toMatch(
      /due today/,
    )
    expect(sentenceOf(noticeOf({ kind: 'overdue', due_date: '2026-11-01' }), today)).toBe(
      'Unit U-0001 is overdue: it was due 2026-11-01.',
    )
    expect(sentenceOf(noticeOf({ kind: 'request_declined' }), today)).toMatch(/declined/)
    expect(sentenceOf(noticeOf({ kind: 'request_done' }), today)).toMatch(/moved to another SME/)
    expect(sentenceOf(noticeOf({ kind: 'assigned' }), today)).toBe(
      'Unit U-0001 was assigned to you, due 2026-11-20.',
    )
  })
})
