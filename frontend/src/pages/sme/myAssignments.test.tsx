import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { unitBooklet, unitDetailOf } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'
import { myUnits, smeSession, summaryOf } from '../../test/smeFixtures'

afterEach(() => vi.unstubAllGlobals())

async function table() {
  return screen.findByRole('table', { name: 'My units' })
}

async function codes(): Promise<string[]> {
  return within(await table())
    .getAllByRole('rowheader')
    .map((h) => h.textContent?.match(/U-\d{4}/)?.[0] ?? '')
}

describe('My Assignments (P31, D216)', () => {
  it('shows the figures of the two cards', async () => {
    smeSession()
    renderApp('/sme')
    const total = await screen.findByRole('region', { name: 'Total Assigned' })
    expect(await within(total).findByText('Lifetime: 4')).toBeInTheDocument()
    expect(within(total).getByText(/academic year 2026-27/).parentElement).toHaveTextContent(
      '3 assignments in academic year 2026-27',
    )
    expect(within(total).getByText('Completed').parentElement).toHaveTextContent('Completed3')
    const history = screen.getByRole('region', { name: 'Historical (all assignments)' })
    expect(within(history).getByText('75.0% completion rate')).toBeInTheDocument()
    expect(within(history).getByText('3 / 4 completed')).toBeInTheDocument()
    expect(within(history).getByRole('img')).toHaveAccessibleName(
      'Frozen: 1, Submitted: 2, Open: 1',
    )
    expect(within(history).getByText('2.5 days')).toBeInTheDocument()
    expect(within(history).getByRole('link', { name: /Full analytics/ })).toHaveAttribute(
      'href',
      '/sme/reports',
    )
  })

  it('says "—" when nothing is handed in yet', async () => {
    smeSession({
      'GET /api/v1/me/assignments/summary': {
        json: summaryOf({
          units_lifetime: 0,
          units_current_year: 0,
          completed: 0,
          active: 0,
          completion_rate: null,
          breakdown: { frozen: 0, submitted: 0, open: 0 },
          average_turnaround_days: null,
          turnaround_units: 0,
        }),
      },
      'GET /api/v1/units': { json: [] },
    })
    renderApp('/sme')
    const history = await screen.findByRole('region', { name: 'Historical (all assignments)' })
    expect(await within(history).findByText('— completion rate')).toBeInTheDocument()
    expect(within(history).getByText('—')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'No units assigned to you' })).toBeVisible()
    expect(screen.getByText('No open unit to evaluate')).toHaveAttribute('aria-disabled', 'true')
  })

  it('lists the units with subject, due date, overdue mark, progress and state', async () => {
    smeSession()
    renderApp('/sme')
    const rows = within(await table())
      .getAllByRole('row')
      .slice(1)
    expect(await codes()).toEqual(['U-0001', 'U-0003', 'U-0002']) // due date, earliest first
    const first = rows[0]!
    expect(within(first).getByText('Physics mid-term')).toBeInTheDocument()
    expect(within(first).getByText('Physics')).toBeInTheDocument()
    expect(within(first).getByText('Overdue')).toBeInTheDocument()
    expect(within(first).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25')
    expect(within(first).getByText('Open')).toBeInTheDocument()
    // A submitted unit is not late and has no Delegate or Submit.
    const submitted = rows[1]!
    expect(within(submitted).getByText('Submitted')).toBeInTheDocument()
    expect(within(submitted).queryByText('Overdue')).toBeNull()
    expect(within(submitted).queryByRole('button', { name: /Delegate|Submit/ })).toBeNull()
    expect(within(submitted).getByRole('link', { name: 'Evaluate U-0003' })).toHaveAttribute(
      'href',
      '/sme/units/unit-3',
    )
  })

  it('sorts by due date, subject and completion, both ways, from the address', async () => {
    smeSession()
    const user = userEvent.setup()
    renderApp('/sme?sort=subject-desc')
    expect(await codes()).toEqual(['U-0001', 'U-0003', 'U-0002']) // Physics, Physics, Maths
    const order = screen.getByLabelText('Order by')
    expect(order).toHaveValue('subject-desc')
    const expected: [string, string[]][] = [
      ['due-asc', ['U-0001', 'U-0003', 'U-0002']],
      ['due-desc', ['U-0002', 'U-0003', 'U-0001']],
      ['subject-asc', ['U-0002', 'U-0001', 'U-0003']],
      ['subject-desc', ['U-0001', 'U-0003', 'U-0002']],
      ['completion-desc', ['U-0003', 'U-0002', 'U-0001']],
      ['completion-asc', ['U-0001', 'U-0002', 'U-0003']],
    ]
    for (const [value, list] of expected) {
      await user.selectOptions(order, value)
      expect(order).toHaveValue(value)
      expect(await codes()).toEqual(list)
    }
  })

  it('filters the table to open units from the Active figure and by state', async () => {
    smeSession()
    const user = userEvent.setup()
    renderApp('/sme')
    expect(await codes()).toHaveLength(3)
    await user.click(screen.getByRole('button', { name: /Active/ }))
    expect(screen.getByLabelText('State')).toHaveValue('open')
    expect(await codes()).toEqual(['U-0001', 'U-0002'])
    await user.selectOptions(screen.getByLabelText('State'), 'frozen')
    expect(await screen.findByRole('heading', { name: 'No units in this state' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Show all units' }))
    expect(await codes()).toHaveLength(3)
  })

  it('reads the filter from the address too', async () => {
    smeSession()
    renderApp('/sme?state=submitted')
    expect(await codes()).toEqual(['U-0003'])
  })

  it('opens the student list of the unit (P32)', async () => {
    const empty = { ...myUnits[1]!, counts: { ...myUnits[1]!.counts, total: 0, completed: 0 } }
    smeSession({ 'GET /api/v1/units/unit-2': { json: unitDetailOf([], empty) } })
    const user = userEvent.setup()
    renderApp('/sme')
    await user.click(await screen.findByRole('link', { name: 'Evaluate U-0002' }))
    expect(await screen.findByRole('heading', { name: 'Algebra final' })).toBeVisible()
    expect(screen.getByText('No booklets yet')).toBeVisible()
  })

  it('starts evaluating the open unit due first from the guidelines card', async () => {
    smeSession()
    renderApp('/sme')
    const card = await screen.findByRole('region', {
      name: 'AI-assisted evaluation and question schema blueprinting',
    })
    expect(
      await within(card).findByRole('link', { name: /Start evaluating U-0001/ }),
    ).toHaveAttribute('href', '/sme/units/unit-1')
    expect(within(card).getByRole('link', { name: 'Manage Schema' })).toHaveAttribute(
      'href',
      '/sme/schema',
    )
  })

  it('delegates with a suggested replacement and a reason (D218)', async () => {
    const mock = smeSession({
      'GET /api/v1/units/unit-2/delegates': { json: [{ id: 't-3', name: 'Kiran Shah' }] },
      'POST /api/v1/units/unit-2/delegation-requests': { json: { id: 'req-1' } },
    })
    const user = userEvent.setup()
    renderApp('/sme')
    await user.click(await screen.findByRole('button', { name: 'Delegate U-0002' }))
    const dialog = screen.getByRole('dialog', { name: /Delegate U-0002/ })
    expect(dialog).toHaveTextContent('U-0002')
    const pick = within(dialog).getByLabelText('Suggested replacement (optional)')
    await within(dialog).findByRole('option', { name: 'Kiran Shah' })
    expect(
      within(pick)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['No suggestion', 'Kiran Shah'])
    const send = within(dialog).getByRole('button', { name: 'Submit request to admin' })
    expect(send).toBeDisabled() // the reason is required
    await user.selectOptions(pick, 't-3')
    await user.type(within(dialog).getByLabelText('Reason for transfer'), 'On leave')
    await user.click(send)
    await waitFor(() =>
      expect(mock.callsTo('POST /api/v1/units/unit-2/delegation-requests')[0]?.body).toEqual({
        reason: 'On leave',
        suggested_to: 't-3',
      }),
    )
  })

  it('sends a request without a suggestion when nobody else has the subject', async () => {
    const mock = smeSession({
      'GET /api/v1/units/unit-2/delegates': { json: [] },
      'POST /api/v1/units/unit-2/delegation-requests': { json: { id: 'req-1' } },
    })
    const user = userEvent.setup()
    renderApp('/sme')
    await user.click(await screen.findByRole('button', { name: 'Delegate U-0002' }))
    const dialog = screen.getByRole('dialog', { name: /Delegate U-0002/ })
    expect(await within(dialog).findByText(/No other active SME has Mathematics/)).toBeVisible()
    await user.type(within(dialog).getByLabelText('Reason for transfer'), 'On leave')
    await user.click(within(dialog).getByRole('button', { name: 'Submit request to admin' }))
    await waitFor(() =>
      expect(mock.callsTo('POST /api/v1/units/unit-2/delegation-requests')[0]?.body).toEqual({
        reason: 'On leave',
      }),
    )
  })

  it('withdraws a waiting request from the table', async () => {
    const waiting = {
      ...myUnits[1]!,
      pending_request: { id: 'req-9', requested_at: '2026-11-01T08:00:00Z' },
    }
    const mock = smeSession({
      'GET /api/v1/units': { json: [myUnits[0]!, waiting, myUnits[2]!] },
      'POST /api/v1/delegation-requests/req-9/withdraw': { json: { id: 'req-9' } },
    })
    const user = userEvent.setup()
    renderApp('/sme')
    expect(await screen.findByText('Reassignment requested')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Delegate U-0002' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Withdraw the request for U-0002' }))
    await waitFor(() =>
      expect(mock.callsTo('POST /api/v1/delegation-requests/req-9/withdraw')).toHaveLength(1),
    )
  })

  it('shows a pending request instead of Delegate', async () => {
    smeSession({
      'GET /api/v1/units': {
        json: [{ ...myUnits[1]!, pending_request: { id: 'req-1', reason: 'Busy' } }],
      },
    })
    renderApp('/sme')
    expect(await screen.findByText('Reassignment requested')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delegate U-0002' })).toBeNull()
  })

  it('opens the submit dialog with the unit’s booklets', async () => {
    const mock = smeSession({
      'GET /api/v1/units/unit-2': {
        json: unitDetailOf([unitBooklet({ id: 'bk-1', status: 'approved' })], {
          ...myUnits[1]!,
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/sme')
    await user.click(await screen.findByRole('button', { name: 'Submit U-0002' }))
    const dialog = await screen.findByRole('dialog', { name: /Submit U-0002/ })
    expect(within(dialog).getByText(/1 of 1/)).toBeInTheDocument()
    expect(mock.callsTo('GET /api/v1/units/unit-2')[0]?.query.get('limit')).toBeNull() // all
  })
})
