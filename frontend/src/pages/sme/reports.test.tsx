import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderApp } from '../../test/render'
import { reportOf, smeSession } from '../../test/smeFixtures'

afterEach(() => vi.unstubAllGlobals())

async function figures() {
  return screen.findByRole('region', { name: /My Operations & Review Reports/ })
}

describe('My Reports (P36, D226)', () => {
  it('shows turnaround (lifetime and this academic year), on-time rate and the current load', async () => {
    smeSession()
    renderApp('/sme/reports')
    expect(await screen.findByRole('heading', { name: 'My Reports', level: 1 })).toBeVisible()
    const cards = within(await figures())
    const turnaround = cards.getByRole('heading', { name: 'Average Turnaround' }).parentElement!
    expect(turnaround).toHaveTextContent('2.5 days')
    expect(turnaround).toHaveTextContent('This academic year (2026-27): 1.8 days')
    expect(turnaround).toHaveTextContent('Over 6 units handed in')
    const onTime = cards.getByRole('heading', { name: 'On-time Rate' }).parentElement!
    expect(onTime).toHaveTextContent('66.7%')
    expect(onTime).toHaveTextContent('of 6 units submitted by their due date')
    const load = cards.getByRole('heading', { name: 'Current Load' }).parentElement!
    expect(load).toHaveTextContent('2 open')
    expect(load).toHaveTextContent('17 booklets pending in your open units')
  })

  it('lists the last four calendar quarters with the current one last', async () => {
    smeSession()
    renderApp('/sme/reports')
    const table = await screen.findByRole('table', {
      name: 'Units submitted and booklets approved per quarter',
    })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(4)
    const cells = rows.map((r) => [
      within(r).getByRole('rowheader').textContent,
      ...within(r)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ])
    expect(cells).toEqual([
      ['2026 Q1', '0', '0'],
      ['2026 Q2', '1', '12'],
      ['2026 Q3', '2', '40'],
      ['2026 Q4(so far)', '3', '25'],
    ])
  })

  it('says "—" and no unit handed in when there is nothing to average', async () => {
    smeSession({
      'GET /api/v1/me/reports': {
        json: reportOf({
          turnaround_lifetime: { average_days: null, units: 0 },
          turnaround_year: { average_days: null, units: 0 },
          on_time_rate: null,
          units_handed_in: 0,
          open_units: 1,
          booklets_pending: 1,
        }),
      },
    })
    renderApp('/sme/reports')
    const cards = within(await figures())
    expect(
      cards.getByRole('heading', { name: 'Average Turnaround' }).parentElement,
    ).toHaveTextContent('—This academic year (2026-27): —')
    const onTime = cards.getByRole('heading', { name: 'On-time Rate' }).parentElement!
    expect(onTime).toHaveTextContent('—')
    expect(onTime).toHaveTextContent('No unit handed in yet')
    expect(cards.getByRole('heading', { name: 'Current Load' }).parentElement).toHaveTextContent(
      '1 booklet pending',
    )
  })

  it('has no accuracy score, speed per hour or marks (D167)', async () => {
    smeSession()
    renderApp('/sme/reports')
    await figures()
    const page = screen.getByRole('main')
    expect(within(page).queryByText(/accuracy/i)).toBeNull()
    expect(within(page).queryByText(/per hour|papers\/hour/i)).toBeNull()
    expect(within(page).queryByText(/\bmarks?\b/i)).toBeNull()
  })

  it('says so when the reports cannot be loaded', async () => {
    smeSession({ 'GET /api/v1/me/reports': { status: 500, json: { detail: 'down' } } })
    renderApp('/sme/reports')
    expect(await screen.findByRole('alert')).toHaveTextContent('Your reports could not be loaded')
  })

  it('is linked from My Assignments as the full analytics', async () => {
    smeSession()
    renderApp('/sme')
    const link = await screen.findByRole('link', { name: /Full analytics/ })
    expect(link).toHaveAttribute('href', '/sme/reports')
  })
})
