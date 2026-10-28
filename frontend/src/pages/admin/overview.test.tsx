import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { healthy } from '../../test/fixtures'
import { adminSession, dashboardOf, systemOf } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'

afterEach(() => vi.unstubAllGlobals())

const FIGURES = dashboardOf({
  smes: { active: 3, idle: 2, suspended: 1 },
  units: { open: 4, submitted: 2, frozen: 5, overdue: 1 },
  booklets: { processing: 7, ready: 6, in_review: 5, approved: 20, attention: 2, total: 40 },
  activity: [
    {
      at: '2026-10-28T10:00:00Z',
      action: 'unit.transferred',
      actor_name: 'Dr. Admin',
      unit_code: 'U-0003',
    },
    {
      at: '2026-10-28T09:00:00Z',
      action: 'subject.granted',
      actor_name: 'Dr. Admin',
      unit_code: null,
    },
    {
      at: '2026-10-28T08:00:00Z',
      action: 'unit.submitted',
      actor_name: 'Asha Rao',
      unit_code: 'U-0001',
    },
  ],
})

function figures(name: string) {
  return screen.getByRole('region', { name })
}

describe('Dashboard Overview', () => {
  it('shows SMEs, units and booklets by stage, with the overdue units', async () => {
    adminSession({ 'GET /api/v1/overview': { json: FIGURES } })
    renderApp('/admin')
    const smes = await screen.findByRole('region', { name: 'SMEs' })
    expect(within(smes).getByText('Active').nextElementSibling).toHaveTextContent('3')
    expect(within(smes).getByText('Idle').nextElementSibling).toHaveTextContent('2')
    expect(within(smes).getByText('Suspended').nextElementSibling).toHaveTextContent('1')
    const units = figures('Units')
    for (const [label, value] of [
      ['Open', '4'],
      ['Submitted', '2'],
      ['Frozen', '5'],
      ['Overdue', '1'],
    ] as const) {
      expect(within(units).getByText(label).nextElementSibling).toHaveTextContent(value)
    }
    const stages = figures('Booklets by stage')
    for (const [label, value] of [
      ['Processing', '7'],
      ['Ready to mark', '6'],
      ['In review', '5'],
      ['Approved', '20'],
    ] as const) {
      expect(within(stages).getByText(label).nextElementSibling).toHaveTextContent(value)
    }
    expect(within(stages).getByText(/2 booklets need a retake or failed/)).toBeInTheDocument()
    expect(within(units).getByRole('link', { name: /View detailed reports/ })).toHaveAttribute(
      'href',
      '/admin/progress',
    )
  })

  it('lists recent activity in words, with no student data and no marks', async () => {
    adminSession({ 'GET /api/v1/overview': { json: FIGURES } })
    renderApp('/admin')
    await screen.findByText('Dr. Admin transferred U-0003')
    const feed = screen.getByRole('region', { name: 'Recent activity' })
    expect(within(feed).getByText('Dr. Admin granted a subject')).toBeInTheDocument()
    expect(within(feed).getByText('Asha Rao submitted U-0001')).toBeInTheDocument()
    expect(within(feed).getAllByRole('listitem')).toHaveLength(3)
    // D167: nothing in the dashboard is a mark or a speed.
    expect(screen.queryByText(/\bmarks\b|\bscores?\b/i)).toBeNull()
    expect(screen.queryByText(/per hour|speed|accuracy/i)).toBeNull()
  })

  it('says so when nothing has happened yet', async () => {
    adminSession()
    renderApp('/admin')
    expect(await screen.findByText(/Nothing yet\. Units, transfers/)).toBeInTheDocument()
    expect(screen.getByText('No booklets uploaded yet.')).toBeInTheDocument()
  })

  it('shows the health box: API, worker, queue depth and the storage used, with no limit', async () => {
    adminSession({
      'GET /api/v1/overview/system': {
        json: systemOf({ queue_queued: 3, queue_running: 1, storage_bytes: 5 * 1024 * 1024 }),
      },
    })
    renderApp('/admin')
    const health = await screen.findByRole('region', { name: 'System health' })
    expect(await within(health).findByText('3 waiting · 1 running')).toBeInTheDocument()
    expect(within(health).getByText('5.0 MB')).toBeInTheDocument()
    expect(within(health).getByText('Running (version 0.0.0)')).toBeInTheDocument()
    expect(within(health).getByText('Running')).toBeInTheDocument()
    expect(within(health).getByText('No storage limit is set yet.')).toBeInTheDocument()
  })

  it('shows a worker that is down and an API that cannot be reached', async () => {
    adminSession({
      'GET /api/v1/health': {
        json: { ...healthy, worker: { status: 'down', detail: 'Worker not reachable' } },
      },
    })
    renderApp('/admin')
    const health = await screen.findByRole('region', { name: 'System health' })
    expect(await within(health).findByText('Not reachable')).toBeInTheDocument()
  })

  it('shows an error, not zeros, when the figures cannot be loaded', async () => {
    adminSession({ 'GET /api/v1/overview': { status: 500, json: { detail: 'broken' } } })
    renderApp('/admin')
    expect(await screen.findByText(/Could not load the figures/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'SMEs' })).toBeNull()
  })
})
