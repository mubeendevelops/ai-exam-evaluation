import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { components } from '../../api/schema'
import { COUNTS, unitBooklet, unitDetailOf } from '../../test/adminFixtures'
import type { Call } from '../../test/mockApi'
import { renderApp } from '../../test/render'
import { myUnits, smeSession } from '../../test/smeFixtures'

type Booklet = components['schemas']['BookletOut']

afterEach(() => vi.unstubAllGlobals())

const UNIT = myUnits[0]! // U-0001, open, Physics mid-term
const URL_ = `GET /api/v1/units/${UNIT.id}`

// Invented names only: no student data in the repository.
function row(n: number, over: Partial<Booklet> = {}): Booklet {
  return unitBooklet({
    id: `bk-${n}`,
    student: { id: `st-${n}`, name: `Student ${n}`, usn: `TST26X${String(n).padStart(4, '0')}` },
    unit: { id: UNIT.id, code: UNIT.code },
    ...over,
  })
}

const ROWS = [
  row(1, {
    status: 'approved',
    row_status: 'completed',
    answers: { attempted: 4, approved: 4 },
  }),
  row(2, { status: 'in_review', row_status: 'in_review', answers: { attempted: 4, approved: 1 } }),
  row(3, { status: 'scored', row_status: 'ready', answers: { attempted: 4, approved: 0 } }),
  row(4, { status: 'needs_retake', row_status: 'needs_retake' }),
  row(5, {
    status: 'failed',
    row_status: 'failed',
    failure_reason: 'reading_failed',
  }),
]

/** The server's student list: it filters and pages (the test checks what was asked). */
function server(total = ROWS.length) {
  return (call: Call) => {
    const offset = Number(call.query.get('offset') ?? 0)
    const all = Array.from({ length: total }, (_, k) => ROWS[k] ?? row(k + 1))
    const page = call.query.get('limit') ? all.slice(offset, offset + 25) : all
    return {
      json: unitDetailOf(page, {
        ...UNIT,
        counts: { ...COUNTS, total, completed: 1 },
        matching: total,
      }),
    }
  }
}

describe('Student list (P32, D217)', () => {
  it('lists each student with status, approved answers and the next step, from one call', async () => {
    const mock = smeSession({ [URL_]: server() })
    renderApp(`/sme/units/${UNIT.id}`)
    expect(await screen.findByRole('heading', { name: 'Physics mid-term' })).toBeVisible()
    const table = screen.getByRole('table', { name: 'Students of the unit' })
    const lines = within(table).getAllByRole('row').slice(1)
    expect(lines.map((r) => within(r).getAllByRole('cell')[1]?.textContent)).toEqual([
      'Completed',
      'In review',
      'Ready',
      'Needs retake',
      'Failed',
    ])
    expect(within(lines[0]!).getByText('4 of 4')).toBeInTheDocument()
    expect(within(lines[1]!).getByText('1 of 4')).toBeInTheDocument()
    expect(
      within(lines[0]!).getByRole('link', { name: 'View final of Student 1' }),
    ).toHaveAttribute('href', '/evaluate?booklet=bk-1&step=3&view=summary')
    expect(
      within(lines[1]!).getByRole('link', { name: 'Review questions of Student 2' }),
    ).toHaveAttribute('href', `/sme/units/${UNIT.id}/booklets/bk-2`)
    expect(within(lines[3]!).getByText(/Waiting for a retake/)).toBeInTheDocument()
    expect(within(lines[4]!).getByText('Reading the handwriting failed.')).toBeInTheDocument()
    expect(screen.getByText('Showing 1–5 of 5 students')).toBeInTheDocument()
    expect(mock.callsTo(URL_)).toHaveLength(1)
    const asked = mock.callsTo(URL_)[0]!.query
    expect([asked.get('offset'), asked.get('limit')]).toEqual(['0', '25'])
  })

  it('filters by name or roll number and by status, kept in the address for the way back', async () => {
    const mock = smeSession({ [URL_]: server() })
    const user = userEvent.setup()
    renderApp(`/sme/units/${UNIT.id}`)
    await screen.findByRole('table', { name: 'Students of the unit' })
    await user.type(screen.getByLabelText('Search student name or roll no.'), 'tst26x0002')
    await waitFor(() => expect(mock.callsTo(URL_).at(-1)?.query.get('q')).toBe('tst26x0002'))
    await user.selectOptions(screen.getByLabelText('Status'), 'in_review')
    await waitFor(() =>
      expect(mock.callsTo(URL_).at(-1)?.query.getAll('status')).toEqual(['in_review']),
    )
    // The question list's link carries the filter back.
    const link = await screen.findByRole('link', { name: 'Review questions of Student 2' })
    const from = new URL(link.getAttribute('href')!, 'http://x').searchParams.get('from')
    expect(new URLSearchParams(from!).get('q')).toBe('tst26x0002')
    expect(new URLSearchParams(from!).get('status')).toBe('in_review')
  })

  it('pages 25 at a time', async () => {
    const mock = smeSession({ [URL_]: server(30) })
    const user = userEvent.setup()
    renderApp(`/sme/units/${UNIT.id}`)
    expect(await screen.findByText('Showing 1–25 of 30 students')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prev' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Showing 26–30 of 30 students')).toBeInTheDocument()
    expect(mock.callsTo(URL_).at(-1)?.query.get('offset')).toBe('25')
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('opens the submit dialog with every booklet and asks a reason for each not marked', async () => {
    const mock = smeSession({
      [URL_]: server(),
      [`POST /api/v1/units/${UNIT.id}/submit`]: { json: { ...UNIT, state: 'submitted' } },
    })
    const user = userEvent.setup()
    renderApp(`/sme/units/${UNIT.id}`)
    await user.click(await screen.findByRole('button', { name: 'Submit unit' }))
    const dialog = await screen.findByRole('dialog', { name: /Submit U-0001/ })
    expect(mock.callsTo(URL_).at(-1)?.query.get('limit')).toBeNull() // every booklet
    expect(dialog).toHaveTextContent('1 of 5 booklets are marked.')
    const send = within(dialog).getByRole('button', { name: 'Submit unit' })
    for (const n of [2, 3, 4, 5]) {
      await user.selectOptions(within(dialog).getByLabelText(`Reason for TST26X000${n}`), 'absent')
    }
    await user.click(send)
    await waitFor(() =>
      expect(mock.callsTo(`POST /api/v1/units/${UNIT.id}/submit`)[0]?.body).toEqual({
        expected_version: 1,
        unmarked: [2, 3, 4, 5].map((n) => ({ booklet_id: `bk-${n}`, reason: 'absent', note: '' })),
      }),
    )
  })

  it('opens Delegate from the student list', async () => {
    smeSession({
      [URL_]: server(),
      [`GET /api/v1/units/${UNIT.id}/delegates`]: { json: [{ id: 't-3', name: 'Kiran Shah' }] },
    })
    const user = userEvent.setup()
    renderApp(`/sme/units/${UNIT.id}`)
    await user.click(await screen.findByRole('button', { name: 'Delegate' }))
    expect(await screen.findByRole('dialog', { name: /Delegate U-0001/ })).toBeVisible()
  })

  it('shows a returned unit with the admin note', async () => {
    smeSession({
      [URL_]: {
        json: unitDetailOf([row(1)], {
          ...UNIT,
          submission: {
            id: 's-1',
            submitted_by: { id: 't-1', name: 'Me' },
            submitted_at: '2026-10-28T08:00:00Z',
            unmarked: [],
            returned_at: '2026-10-28T09:00:00Z',
            returned_by: { id: 'a-1', name: 'Admin' },
            return_note: 'Recheck question 3',
          },
        }),
      },
    })
    renderApp(`/sme/units/${UNIT.id}`)
    expect(
      await screen.findByText(/Your admin returned this unit: Recheck question 3/),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Submit unit' })).toBeVisible()
  })

  it('shows a submitted unit as handed over, with no Submit or Delegate', async () => {
    smeSession({ 'GET /api/v1/units/unit-3': { json: unitDetailOf([row(1)], { ...myUnits[2]! }) } })
    renderApp('/sme/units/unit-3')
    expect(await screen.findByText('Submitted to your admin')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Submit unit' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Delegate' })).toBeNull()
  })

  it("says so for another SME's unit (404)", async () => {
    smeSession({ 'GET /api/v1/units/nope': { status: 404, json: { detail: 'Not found.' } } })
    renderApp('/sme/units/nope')
    expect(await screen.findByText('Unit not found')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to My Assignments' })).toHaveAttribute(
      'href',
      '/sme',
    )
  })
})
