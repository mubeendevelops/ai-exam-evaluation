import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  COUNTS,
  adminSession,
  asha,
  physics,
  unitBooklet,
  unitDetailOf,
  unitOf,
} from '../../test/adminFixtures'
import { renderApp } from '../../test/render'

const PNG = {
  body: new Blob(['x'], { type: 'image/png' }),
  headers: { 'Content-Type': 'image/png' },
}

beforeEach(() => {
  Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const paper = {
  ...unitOf().paper,
  subject: { id: physics.id, code: physics.code, name: physics.name },
}
const pdf = (name: string) => new File(['%PDF-1.7 synthetic'], name, { type: 'application/pdf' })

describe('upload & assign papers: units', () => {
  it('lists the units with their paper, SME and progress, and filters them', async () => {
    const mock = adminSession({
      'GET /api/v1/units': {
        json: [unitOf({ counts: { ...COUNTS, total: 4, completed: 1, needs_retake: 1 } })],
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload')
    const table = await screen.findByRole('table', { name: 'Units of work' })
    const row = within(table).getByRole('row', { name: /U-0001/ })
    expect(row).toHaveTextContent('PHY-2026-MID')
    expect(row).toHaveTextContent('Asha Rao')
    expect(row).toHaveTextContent('1 of 4 completed')
    expect(row).toHaveTextContent('1 need a retake')
    expect(within(row).getByRole('link', { name: 'U-0001' })).toHaveAttribute(
      'href',
      '/admin/upload/units/unit-1',
    )
    await user.selectOptions(screen.getAllByLabelText('SME')[0]!, asha.id)
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/units').at(-1)?.query.get('owner_id')).toBe(asha.id),
    )
  })

  it('creates a unit, offering only the papers and SMEs of the subject', async () => {
    const mock = adminSession({
      'GET /api/v1/units/papers': { json: [paper] },
      'GET /api/v1/units/smes': {
        json: [{ id: asha.id, name: asha.display_name, email: asha.email }],
      },
      'POST /api/v1/units': { status: 201, json: unitOf() },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload')
    await user.click(await screen.findByRole('button', { name: /Create unit/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Create a unit' })
    const send = within(dialog).getByRole('button', { name: 'Create unit' })
    expect(send).toBeDisabled()
    // Retired subjects are not offered.
    const subject = within(dialog).getByLabelText('Subject')
    expect(within(subject).queryByRole('option', { name: /Chemistry/ })).toBeNull()
    await user.selectOptions(subject, physics.id)
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/units/smes')[0]?.query.get('subject_id')).toBe(physics.id),
    )
    await user.selectOptions(within(dialog).getByLabelText('Paper'), paper.id)
    await user.type(within(dialog).getByLabelText('Student year'), '2024')
    await user.type(within(dialog).getByLabelText('Due date'), '2030-01-31')
    const sme = within(dialog).getByLabelText('SME')
    expect(within(sme).getAllByRole('option')).toHaveLength(2) // the prompt and Asha only
    await user.selectOptions(sme, asha.id)
    await user.click(send)
    expect(await screen.findByText(/Unit U-0001 created; Asha Rao was emailed/)).toBeVisible()
    expect(mock.callsTo('POST /api/v1/units')[0]?.body).toEqual({
      paper_id: paper.id,
      year: '2024',
      owner_id: asha.id,
      due_date: '2030-01-31',
    })
  })

  it('says why a subject offers nothing', async () => {
    adminSession({
      'GET /api/v1/units/papers': { json: [] },
      'GET /api/v1/units/smes': { json: [] },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload')
    await user.click(await screen.findByRole('button', { name: /Create unit/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Create a unit' })
    await user.selectOptions(within(dialog).getByLabelText('Subject'), physics.id)
    expect(await within(dialog).findByText(/No paper of this subject is ready/)).toBeVisible()
    expect(within(dialog).getByText(/No active SME has this subject/)).toBeVisible()
  })
})

describe('upload & assign papers: one unit', () => {
  it('uploads several PDFs and lists what happened to each, with a way to Students', async () => {
    const mock = adminSession({
      'GET /api/v1/units/unit-1': { json: unitDetailOf() },
      'POST /api/v1/units/unit-1/booklets': {
        json: {
          files: [
            {
              name: '1AB21CS001.pdf',
              outcome: 'queued',
              problem: null,
              message: null,
              usn: '1AB21CS001',
              student_id: 's-1',
              booklet_id: 'bk-1',
            },
            {
              name: 'NOBODY01.pdf',
              outcome: 'refused',
              problem: 'unknown_usn',
              message: 'No student with this roll number: add or correct it in Students.',
              usn: 'NOBODY01',
              student_id: null,
              booklet_id: null,
            },
          ],
          queued: 1,
          refused: 1,
          not_queued: 0,
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/units/unit-1')
    expect(await screen.findByRole('heading', { name: 'Unit U-0001', level: 1 })).toBeVisible()
    expect(screen.getByText('No booklets yet')).toBeVisible()
    const zone = screen.getByRole('button', { name: /Drop the students' PDFs/ })
    expect(within(zone).getByText(/5 free queue slots/)).toBeVisible()
    const input = screen.getByLabelText('Answer sheet files')
    await user.upload(input, [pdf('1AB21CS001.pdf'), pdf('NOBODY01.pdf')])
    const results = await screen.findByRole('region', { name: 'Upload results' })
    expect(within(results).getByText('Queued')).toBeVisible()
    expect(within(results).getByText(/No student with this roll number/)).toBeVisible()
    expect(within(results).getByRole('link', { name: 'Students' })).toHaveAttribute(
      'href',
      '/admin/upload/students',
    )
    expect(mock.callsTo('POST /api/v1/units/unit-1/booklets')).toHaveLength(1)
    expect(await screen.findByText('1 queued, 1 refused, 0 not queued.')).toBeVisible()
  })

  it('refuses anything but PDFs before sending, and stops at a full queue', async () => {
    const mock = adminSession({ 'GET /api/v1/units/unit-1': { json: unitDetailOf() } })
    const user = userEvent.setup({ applyAccept: false })
    renderApp('/admin/upload/units/unit-1')
    await screen.findByRole('heading', { name: 'Unit U-0001', level: 1 })
    const photo = new File(['x'], '1AB21CS001.jpg', { type: 'image/jpeg' })
    await user.upload(screen.getByLabelText('Answer sheet files'), [photo])
    expect(await screen.findByRole('alert')).toHaveTextContent('Only PDFs')
    expect(mock.callsTo('POST /api/v1/units/unit-1/booklets')).toHaveLength(0)
  })

  it('shows a full queue', async () => {
    adminSession({ 'GET /api/v1/units/unit-1': { json: unitDetailOf([], { waiting: 5 }) } })
    renderApp('/admin/upload/units/unit-1')
    const zone = await screen.findByRole('button', { name: /Drop the students' PDFs/ })
    expect(zone).toHaveAttribute('aria-disabled', 'true')
    expect(within(zone).getByText(/5 booklets are waiting/)).toBeVisible()
  })

  it('shows a flagged page to the admin, uses it anyway, or replaces the file', async () => {
    const flagged = unitBooklet({ status: 'needs_retake', flagged_pages: [2], page_count: 2 })
    const page = (number: number, extra: Record<string, unknown> = {}) => ({
      number,
      cleaned: true,
      width: 1000,
      height: 1400,
      retake_reasons: [],
      use_anyway: false,
      sharpness: 200,
      glare_share: 0,
      rotation_degrees: 0,
      rotation_guessed: false,
      skew_degrees: 0,
      cropped: false,
      perspective_corrected: false,
      neighbour_removed: false,
      page_found: true,
      image_url: null,
      original_url: null,
      text_read: false,
      needs_text: false,
      ocr_failures: [],
      text_url: null,
      ...extra,
    })
    const mock = adminSession({
      'GET /api/v1/units/unit-1': { json: unitDetailOf([flagged]) },
      'GET /api/v1/booklets/bk-1': {
        json: {
          ...flagged,
          pages: [
            page(1),
            page(2, {
              retake_reasons: ['blurry'],
              image_url: '/api/v1/booklets/bk-1/pages/2/image',
            }),
          ],
        },
      },
      'GET /api/v1/booklets/bk-1/pages/2/image': PNG,
      'POST /api/v1/booklets/bk-1/pages/2/use-anyway': { json: flagged },
      'POST /api/v1/booklets/bk-1/replace': { status: 201, json: unitBooklet({ id: 'bk-2' }) },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/units/unit-1')
    await user.click(await screen.findByRole('button', { name: 'Retake 1AB21CS001' }))
    const panel = await screen.findByRole('region', { name: 'Retake for 1AB21CS001' })
    expect(await within(panel).findByText('Page 2')).toBeVisible()
    expect(within(panel).getByText(/The writing is blurred/)).toBeVisible()
    // The flagged page's cleaned image is fetched for the admin (P26), the passed page's not.
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/booklets/bk-1/pages/2/image')).toHaveLength(1),
    )
    await user.click(within(panel).getByRole('button', { name: 'Use page 2 anyway' }))
    expect(await screen.findByText('Going on with page 2 of 1AB21CS001.')).toBeVisible()
    expect(mock.callsTo('POST /api/v1/booklets/bk-1/pages/2/use-anyway')).toHaveLength(1)
    await user.upload(within(panel).getByLabelText('New PDF for 1AB21CS001'), pdf('new.pdf'))
    expect(await screen.findByText('1AB21CS001: the new PDF is queued.')).toBeVisible()
    expect(mock.callsTo('POST /api/v1/booklets/bk-1/replace')).toHaveLength(1)
  })
})

describe('upload & assign papers: a submitted unit (P28)', () => {
  it('shows what the SME handed over and returns it with a note', async () => {
    const submitted = unitDetailOf([unitBooklet({ id: 'bk-2', status: 'in_review' })], {
      state: 'submitted',
      version: 4,
      submission: {
        id: 's-1',
        submitted_by: { id: asha.id, name: asha.display_name },
        submitted_at: '2026-10-28T10:00:00Z',
        unmarked: [{ booklet_id: 'bk-2', reason: 'absent', note: '' }],
        returned_at: null,
        returned_by: null,
        return_note: '',
      },
    })
    const mock = adminSession({
      'GET /api/v1/units/unit-1': { json: submitted },
      'POST /api/v1/units/unit-1/return': { json: unitOf({ state: 'open' }) },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/units/unit-1')
    const panel = await screen.findByRole('region', { name: /Submission/ })
    expect(within(panel).getByRole('list', { name: 'Booklets not marked' })).toHaveTextContent(
      'not marked: Absent',
    )
    expect(screen.queryByText("Drop the students' PDFs")).toBeNull()
    await user.click(within(panel).getByRole('button', { name: /Return to SME/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Note to the SME'), 'Mark the absentee again')
    await user.click(within(dialog).getByRole('button', { name: 'Return unit' }))
    await waitFor(() => expect(mock.callsTo('POST /api/v1/units/unit-1/return')).toHaveLength(1))
    expect(mock.callsTo('POST /api/v1/units/unit-1/return')[0]?.body).toEqual({
      note: 'Mark the absentee again',
      expected_version: 4,
    })
  })
})
