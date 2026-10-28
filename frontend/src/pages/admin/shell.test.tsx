import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { healthy, meOf, tokenOf } from '../../test/fixtures'
import { adminSession } from '../../test/adminFixtures'
import { mockApi } from '../../test/mockApi'
import { renderApp } from '../../test/render'

afterEach(() => vi.unstubAllGlobals())

const SECTIONS = [
  'Dashboard Overview',
  'SME Directory & Subjects',
  'Upload & Assign Papers',
  'SME Progress Reports',
  'Redistribute Workload',
  'Audit & External Reports',
]

describe('admin portal shell', () => {
  it('lands on the dashboard with the mock sidebar and none of the SME tabs', async () => {
    adminSession()
    renderApp('/')
    // Signed in with a refresh cookie: the landing page sends the admin home.
    expect(await screen.findByRole('heading', { name: 'Dashboard Overview' })).toBeInTheDocument()
    const sidebar = screen.getByRole('navigation', { name: 'Admin sections' })
    expect(
      within(sidebar)
        .getAllByRole('link')
        .map((l) => l.textContent?.trim()),
    ).toEqual(SECTIONS)
    expect(within(sidebar).getByRole('link', { name: 'Dashboard Overview' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    for (const tab of ['Q&A DB', 'AI Evaluation', 'Evaluated', 'Schema Designer']) {
      expect(screen.queryByRole('link', { name: tab })).toBeNull()
    }
    expect(screen.getByText('Nothing to show yet')).toBeInTheDocument()
  })

  it('shows a short empty state for each section that is not built yet', async () => {
    adminSession()
    const user = userEvent.setup()
    renderApp('/admin')
    const sidebar = await screen.findByRole('navigation', { name: 'Admin sections' })
    const empty: [string, string][] = [
      ['SME Progress Reports', 'No progress to report yet'],
      ['Audit & External Reports', 'No reports yet'],
    ]
    for (const [link, text] of empty) {
      await user.click(within(sidebar).getByRole('link', { name: link }))
      expect(await screen.findByRole('heading', { name: link, level: 1 })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: text })).toBeInTheDocument()
    }
  })

  it('reaches the roster from Upload & Assign Papers and keeps that item highlighted', async () => {
    adminSession()
    const user = userEvent.setup()
    renderApp('/admin/upload')
    await user.click(await screen.findByRole('link', { name: 'Students' }))
    expect(await screen.findByRole('heading', { name: 'Students', level: 1 })).toBeInTheDocument()
    const sidebar = screen.getByRole('navigation', { name: 'Admin sections' })
    expect(within(sidebar).getByRole('link', { name: 'Upload & Assign Papers' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('sends an unknown admin address to the dashboard', async () => {
    adminSession()
    renderApp('/admin/nothing-here')
    expect(await screen.findByRole('heading', { name: 'Dashboard Overview' })).toBeInTheDocument()
  })

  it('keeps the SME on the SME tabs: no sidebar, /admin/smes goes to Q&A', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': { json: tokenOf({ role: 'sme' }) },
      'GET /api/v1/auth/me': { json: meOf({ role: 'sme' }) },
      'GET /api/v1/health': { json: healthy },
    })
    renderApp('/admin/smes')
    expect(
      await screen.findByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Admin sections' })).toBeNull()
  })

  it('lets the Governor see only the placeholder', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': { json: tokenOf({ role: 'governor' }) },
      'GET /api/v1/auth/me': { json: meOf({ role: 'governor' }) },
      'GET /api/v1/health': { json: healthy },
    })
    renderApp('/admin')
    expect(await screen.findByRole('heading', { name: 'Governor portal' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Admin sections' })).toBeNull()
  })
})
