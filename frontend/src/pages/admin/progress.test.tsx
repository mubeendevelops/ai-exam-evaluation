import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { adminSession, kiran, smeRowOf, unitRowOf } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'

afterEach(() => vi.unstubAllGlobals())

const SMES = [
  smeRowOf(),
  smeRowOf({
    user_id: kiran.id,
    display_name: kiran.display_name,
    status: 'idle',
    units_owned: 2,
    open_units: 0,
    booklets: 8,
    approved: 8,
    pending: 0,
    last_activity: null,
  }),
  smeRowOf({
    user_id: 't-9',
    display_name: 'Meera Iyer',
    units_owned: 3,
    open_units: 3,
    overdue_units: 2,
    booklets: 300,
    approved: 90,
    pending: 210,
    heavy_load: true,
  }),
]
const UNITS = [
  unitRowOf(),
  unitRowOf({
    unit_id: 'unit-2',
    code: 'U-0002',
    owner_id: kiran.id,
    owner_name: kiran.display_name,
    state: 'submitted',
    booklets: { processing: 0, ready: 0, in_review: 0, approved: 8, attention: 0, total: 8 },
  }),
  unitRowOf({ unit_id: 'unit-3', code: 'U-0003', overdue: true, due_date: '2026-10-01' }),
]

function reportSession() {
  return adminSession({ 'GET /api/v1/overview/progress': { json: { smes: SMES, units: UNITS } } })
}

describe('SME Progress Reports', () => {
  it('lists each SME with units, booklets, approved, pending, overdue and last activity', async () => {
    reportSession()
    renderApp('/admin/progress')
    const table = await screen.findByRole('table', { name: 'Progress of each SME' })
    const asha = within(within(table).getByRole('row', { name: /Asha Rao/ }))
    expect(asha.getAllByRole('cell').map((c) => c.textContent)).toEqual([
      '1',
      '10',
      '4',
      '6',
      '40%',
      '0',
      new Date('2026-10-28T09:30:00Z').toLocaleString(),
    ])
    expect(asha.getByText('Active')).toBeInTheDocument()
    expect(asha.getByRole('progressbar', { name: 'Asha Rao: approved' })).toHaveAttribute(
      'aria-valuenow',
      '40',
    )
    const kirans = within(within(table).getByRole('row', { name: /Kiran Shah/ }))
    expect(kirans.getByText('Idle')).toBeInTheDocument()
    expect(kirans.getByText('No activity yet')).toBeInTheDocument()
  })

  it('flags a heavy load only where the server says so', async () => {
    reportSession()
    renderApp('/admin/progress')
    const table = await screen.findByRole('table', { name: 'Progress of each SME' })
    expect(within(table).getAllByText('Heavy load')).toHaveLength(1)
    expect(
      within(within(table).getByRole('row', { name: /Meera Iyer/ })).getByText('Heavy load'),
    ).toBeInTheDocument()
    expect(
      within(within(table).getByRole('row', { name: /Asha Rao/ })).queryByText('Heavy load'),
    ).toBeNull()
  })

  it('shows each unit with a progress bar, its due date, overdue flag and state', async () => {
    reportSession()
    renderApp('/admin/progress')
    const table = await screen.findByRole('table', { name: 'Progress of each unit' })
    const one = within(within(table).getByRole('row', { name: /U-0001/ }))
    expect(one.getByText('Open')).toBeInTheDocument()
    expect(one.getByText('4 of 10 approved')).toBeInTheDocument()
    expect(one.getByRole('progressbar', { name: 'U-0001: approved' })).toHaveAttribute(
      'aria-valuenow',
      '40',
    )
    expect(one.queryByText('Overdue')).toBeNull()
    const two = within(within(table).getByRole('row', { name: /U-0002/ }))
    expect(two.getByText('Submitted')).toBeInTheDocument()
    expect(two.getByText('8 of 8 approved')).toBeInTheDocument()
    expect(two.getByText('Kiran Shah')).toBeInTheDocument()
    const three = within(within(table).getByRole('row', { name: /U-0003/ }))
    expect(three.getByText('Overdue')).toBeInTheDocument()
    expect(three.getByText('2026-10-01')).toBeInTheDocument()
    expect(within(table).getByRole('link', { name: 'U-0001' })).toHaveAttribute(
      'href',
      '/admin/upload/units/unit-1',
    )
  })

  it('has no marks and no speed column', async () => {
    reportSession()
    renderApp('/admin/progress')
    await screen.findByRole('table', { name: 'Progress of each SME' })
    expect(screen.queryByText(/avg speed|per hour|accuracy/i)).toBeNull()
    expect(screen.queryByRole('columnheader', { name: /marks?|score/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /export/i })).toBeNull()
  })

  it('shows an empty state before any SME exists', async () => {
    adminSession()
    renderApp('/admin/progress')
    expect(await screen.findByRole('heading', { name: 'No progress to report yet' })).toBeVisible()
  })

  it('shows an error when the report cannot be loaded', async () => {
    adminSession({ 'GET /api/v1/overview/progress': { status: 500, json: { detail: 'broken' } } })
    renderApp('/admin/progress')
    expect(await screen.findByText(/Could not load the report/)).toBeInTheDocument()
  })
})

describe('Audit & External Reports', () => {
  it('says reports come later and builds nothing', async () => {
    adminSession()
    renderApp('/admin/reports')
    expect(await screen.findByRole('heading', { name: 'Reports come later' })).toBeInTheDocument()
    expect(screen.getByText(/Nothing has been generated or sent/)).toBeInTheDocument()
  })
})
