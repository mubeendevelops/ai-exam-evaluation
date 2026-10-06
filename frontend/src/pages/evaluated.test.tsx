import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Evaluated } from '../components/evaluated/EvaluatedList'
import { healthy, meOf, tokenOf } from '../test/fixtures'
import { mockApi, type Call } from '../test/mockApi'
import { renderApp } from '../test/render'

const PDF = { body: '%PDF-1.7 synthetic', headers: { 'Content-Type': 'application/pdf' } }

let saved: string[]
beforeEach(() => {
  saved = []
  Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:sheet'), revokeObjectURL: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push(this.download)
  })
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// All names are invented: no student data in the repository.
function item(over: Partial<Evaluated> = {}): Evaluated {
  return {
    id: 'b-1',
    status: 'approved',
    student: { id: 'st-1', name: 'Test Student One', usn: 'TST001' },
    exam: 'Mid-term Physics',
    course_code: 'PHY-101',
    total: 41.5,
    max_marks: 50,
    sheet_version: 1,
    evaluated_at: '2026-10-06T09:00:00Z',
    sheets: [
      {
        version: 1,
        total: 41.5,
        max_marks: 50,
        issued_at: '2026-10-06T09:00:00Z',
        note: '',
        pdf_url: '/api/v1/booklets/b-1/result-sheets/1/pdf',
      },
    ],
    ...over,
  }
}

const amended = item({
  id: 'b-2',
  status: 'approved_amended',
  student: { id: 'st-2', name: 'Test Student Two', usn: 'TST002' },
  total: 44,
  sheet_version: 2,
  sheets: [
    {
      version: 1,
      total: 41.5,
      max_marks: 50,
      issued_at: '2026-10-06T09:00:00Z',
      note: '',
      pdf_url: '/api/v1/booklets/b-2/result-sheets/1/pdf',
    },
    {
      version: 2,
      total: 44,
      max_marks: 50,
      issued_at: '2026-10-06T10:00:00Z',
      note: '3: Recount',
      pdf_url: '/api/v1/booklets/b-2/result-sheets/2/pdf',
    },
  ],
})

const page = (items: Evaluated[], total = items.length) => ({
  json: { items, total, limit: 20, offset: 0 },
})

function signedIn(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf() },
    'GET /api/v1/auth/me': { json: meOf() },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/evaluated-booklets': page([amended, item()]),
    ...extra,
  })
}

async function open() {
  const user = userEvent.setup()
  renderApp('/evaluated')
  const table = await screen.findByRole('table')
  return { user, table }
}

describe('evaluated booklets', () => {
  it('lists approved booklets with their result and every sheet version', async () => {
    signedIn()
    const { table } = await open()
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    const first = rows[0]!
    expect(first).toHaveTextContent('Test Student Two')
    expect(first).toHaveTextContent('TST002')
    expect(first).toHaveTextContent('Mid-term Physics')
    expect(first).toHaveTextContent('PHY-101')
    expect(first).toHaveTextContent('Approved (amended)')
    expect(first).toHaveTextContent('44 / 50')
    expect(within(first).getByRole('button', { name: 'v1 PDF' })).toBeVisible()
    expect(within(first).getByRole('button', { name: 'v2 PDF' })).toBeVisible()
    expect(within(rows[1]!).getAllByRole('button', { name: /PDF/ })).toHaveLength(1)
    expect(
      within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', {
        name: 'Evaluated',
      }),
    ).toHaveAttribute('aria-current', 'page')
  })

  it('searches by exam, student, USN and status', async () => {
    const mock = signedIn()
    const { user } = await open()
    await user.type(screen.getByLabelText('Exam'), 'physics')
    await user.type(screen.getByLabelText('Student'), 'two')
    await user.type(screen.getByLabelText('USN'), 'tst002')
    await user.selectOptions(screen.getByLabelText('Status'), 'approved_amended')
    await waitFor(() => {
      const last = mock.callsTo('GET /api/v1/evaluated-booklets').at(-1) as Call
      expect(Object.fromEntries(last.query)).toMatchObject({
        exam: 'physics',
        student: 'two',
        usn: 'tst002',
        status: 'approved_amended',
        limit: '20',
        offset: '0',
      })
    })
  })

  it('says when nothing matches, and when nothing is evaluated yet', async () => {
    signedIn({ 'GET /api/v1/evaluated-booklets': page([]) })
    const { user } = await (async () => {
      const user = userEvent.setup()
      renderApp('/evaluated')
      return { user }
    })()
    expect(await screen.findByText(/appear here once their marks are approved/)).toBeVisible()
    await user.type(screen.getByLabelText('Student'), 'nobody')
    expect(await screen.findByText(/No approved booklet matches/)).toBeVisible()
  })

  it('pages through the list', async () => {
    const mock = signedIn({ 'GET /api/v1/evaluated-booklets': page([item()], 45) })
    const { user } = await open()
    expect(screen.getByText('1–1 of 45')).toBeVisible()
    expect(screen.getByRole('button', { name: /Previous/ })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /Next/ }))
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/evaluated-booklets').at(-1)?.query.get('offset')).toBe('20'),
    )
  })

  it('downloads a version with the token and saves it under a readable name', async () => {
    const mock = signedIn({
      'GET /api/v1/booklets/b-2/result-sheets/1/pdf': PDF,
      'GET /api/v1/booklets/b-2/result-sheets/2/pdf': PDF,
    })
    const { user, table } = await open()
    const first = within(table).getAllByRole('row')[1]!
    await user.click(within(first).getByRole('button', { name: 'v1 PDF' }))
    await waitFor(() => expect(saved).toEqual(['result-sheet-TST002-v1.pdf']))
    await user.click(within(first).getByRole('button', { name: 'v2 PDF' }))
    await waitFor(() => expect(saved).toHaveLength(2))
    expect(saved[1]).toBe('result-sheet-TST002-v2.pdf')
    expect(
      mock.callsTo('GET /api/v1/booklets/b-2/result-sheets/1/pdf')[0]?.headers.get('Authorization'),
    ).toMatch(/^Bearer /)
  })

  it('says so when a stored PDF is gone', async () => {
    signedIn({ 'GET /api/v1/booklets/b-2/result-sheets/1/pdf': { status: 404, json: {} } })
    const { user, table } = await open()
    await user.click(within(table).getAllByRole('button', { name: 'v1 PDF' })[0]!)
    expect(await screen.findByText('This result sheet has no stored PDF.')).toBeVisible()
  })

  it('opens the booklet summary', async () => {
    signedIn({ 'GET /api/v1/booklets/b-2': { status: 404, json: {} } })
    const { user, table } = await open()
    await user.click(
      within(table).getByRole('button', { name: /Open the booklet of Test Student Two/ }),
    )
    // The evaluation page takes over (its own tests cover what it shows).
    expect(
      await screen.findByRole('heading', { name: 'AI Automated Answer Evaluation Pipeline' }),
    ).toBeVisible()
  })

  describe('deleting', () => {
    it('asks first, says what goes, and does nothing on Cancel', async () => {
      const mock = signedIn()
      const { user, table } = await open()
      await user.click(
        within(table).getByRole('button', { name: /Delete the booklet of Test Student Two/ }),
      )
      const dialog = await screen.findByRole('dialog', { name: 'Delete this evaluated booklet?' })
      expect(dialog).toHaveTextContent('Test Student Two (TST002)')
      expect(dialog).toHaveTextContent('all 2 result sheets are deleted for good')
      expect(dialog).toHaveTextContent('nothing of its content')
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(mock.callsTo('DELETE /api/v1/booklets/b-2')).toHaveLength(0)
    })

    it('deletes after the confirmation and refreshes the list', async () => {
      let gone = false
      const mock = signedIn({
        'GET /api/v1/evaluated-booklets': () => page(gone ? [item()] : [amended, item()]),
        'DELETE /api/v1/booklets/b-2': () => {
          gone = true
          return { status: 204 }
        },
      })
      const { user, table } = await open()
      await user.click(
        within(table).getByRole('button', { name: /Delete the booklet of Test Student Two/ }),
      )
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: 'Delete booklet' }))
      await waitFor(() => expect(mock.callsTo('DELETE /api/v1/booklets/b-2')).toHaveLength(1))
      await waitFor(() => expect(screen.queryByText('Test Student Two')).toBeNull())
      expect(screen.getByText('Test Student One')).toBeVisible()
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(
        await screen.findByText(/A record without content stays in the audit log/),
      ).toBeVisible()
    })

    it('shows the refusal when another teacher has the booklet open', async () => {
      signedIn({
        'DELETE /api/v1/booklets/b-1': {
          status: 423,
          json: { detail: 'Another teacher has this booklet open.' },
        },
      })
      const { user, table } = await open()
      await user.click(
        within(table).getByRole('button', { name: /Delete the booklet of Test Student One/ }),
      )
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: 'Delete booklet' }))
      expect(await within(dialog).findByRole('alert')).toHaveTextContent(
        'Another teacher has this booklet open.',
      )
      expect(screen.getByText('Test Student One')).toBeVisible()
    })
  })
})
