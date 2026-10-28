import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { healthy, meOf, tokenOf } from '../test/fixtures'
import {
  BLUEPRINT,
  BOOKLET,
  PAGE_TEXT,
  SEGMENTS,
  UNIT,
  answer,
  booklet,
  detail,
  graph,
  page,
  region,
  review,
} from '../test/evaluateFixtures'
import { requestOf, unitOf } from '../test/adminFixtures'
import { mockApi } from '../test/mockApi'
import { detail as qnaDetail } from '../test/qnaFixtures'
import { renderApp } from '../test/render'

const B = `/api/v1/booklets/${BOOKLET}`
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

const exam = {
  id: BLUEPRINT,
  version: 1,
  title: 'Mid-term Physics',
  course_code: 'PHY-101',
  subject_id: 's-1',
  subject_name: 'Physics',
  total_marks: 6,
  duration_minutes: 60,
  section_count: 1,
  question_count: 3,
  unlinked_count: 0,
}

const listOf = (items = [booklet()], extra: Record<string, unknown> = {}) => ({
  json: { items, total: items.length, limit: 100, offset: 0, waiting: 0, max_waiting: 5, ...extra },
})

/** The SME's open units (P26): step 1 lists the booklets of the one picked. */
const UNITS = [
  unitOf({ id: UNIT, code: 'U-0001' }),
  unitOf({
    id: 'unit-2',
    code: 'U-0002',
    year: '2025',
    paper: { ...unitOf().paper, exam_code: 'PHY-2026-END' },
  }),
]

function signedIn(extra: Parameters<typeof mockApi>[0] = {}, me = meOf()) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf() },
    'GET /api/v1/auth/me': { json: me },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/blueprints': { json: [exam] },
    'GET /api/v1/units': { json: UNITS },
    'GET /api/v1/booklets': listOf(),
    'GET /api/v1/delegation-requests': { json: [] },
    ...extra,
  })
}

// ---------------------------------------------------------------------------------------------
// Step 1: Scan Upload
// ---------------------------------------------------------------------------------------------

describe('step 1: submitting a unit (P28)', () => {
  it('asks a reason for each booklet not marked, then submits the whole unit', async () => {
    const marked = booklet({ id: 'b-ok', status: 'approved' })
    const absent = booklet({
      id: 'b-no',
      status: 'in_review',
      student: { id: 'st-2', name: 'Test Student Two', usn: 'TST002' },
    })
    const mock = signedIn({
      'GET /api/v1/booklets': listOf([marked, absent]),
      [`POST /api/v1/units/${UNIT}/submit`]: { json: unitOf({ id: UNIT, state: 'submitted' }) },
    })
    const user = userEvent.setup()
    renderApp('/evaluate')
    await screen.findByRole('article', { name: 'Submission Test Student Two' })
    await user.click(screen.getByRole('button', { name: 'Submit unit' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('1 of 2 booklets are marked.')
    const send = within(dialog).getByRole('button', { name: 'Submit unit' })
    expect(send).toBeDisabled()
    await user.selectOptions(within(dialog).getByLabelText('Reason for TST002'), 'other')
    expect(send).toBeDisabled() // "other" needs a note
    await user.type(within(dialog).getByLabelText('Note for TST002'), 'Left the hall')
    await user.click(send)
    await waitFor(() => expect(mock.callsTo(`POST /api/v1/units/${UNIT}/submit`)).toHaveLength(1))
    expect(mock.callsTo(`POST /api/v1/units/${UNIT}/submit`)[0]?.body).toEqual({
      expected_version: 1,
      unmarked: [{ booklet_id: 'b-no', reason: 'other', note: 'Left the hall' }],
    })
  })

  it('shows a submitted unit as handed over, and a returned one with the admin note', async () => {
    const submitted = unitOf({ id: UNIT, code: 'U-0001', state: 'submitted' })
    const returned = unitOf({
      id: 'unit-2',
      code: 'U-0002',
      submission: {
        id: 's-1',
        submitted_by: { id: 't-1', name: 'Me' },
        submitted_at: '2026-10-28T08:00:00Z',
        unmarked: [],
        returned_at: '2026-10-28T09:00:00Z',
        returned_by: { id: 'a-1', name: 'Admin' },
        return_note: 'Recheck question 3',
      },
    })
    signedIn({ 'GET /api/v1/units': { json: [submitted, returned] } })
    const user = userEvent.setup()
    renderApp('/evaluate')
    expect(await screen.findByText('Submitted to your admin')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Submit unit' })).toBeNull()
    await user.selectOptions(screen.getByLabelText('Unit'), 'unit-2')
    expect(
      await screen.findByText(/Your admin returned this unit: Recheck question 3/),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Submit unit' })).toBeVisible()
  })
})

describe('step 1: the booklets of my units', () => {
  it('shows the three steps, my first unit and its booklets, and no upload controls', async () => {
    const mock = signedIn()
    renderApp('/evaluate')
    expect(
      await screen.findByText(/uploaded by your admin into the units assigned to you/),
    ).toBeVisible()
    const steps = screen.getByRole('list', { name: 'Evaluation steps' })
    expect(within(steps).getByRole('button', { name: '1. Scan Upload' })).toHaveAttribute(
      'aria-current',
      'step',
    )
    const picker = await screen.findByLabelText('Unit')
    expect(picker).toHaveValue(UNIT)
    expect(
      within(picker)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['U-0001 · PHY-2026-MID · 2024', 'U-0002 · PHY-2026-END · 2025'])
    expect(
      await screen.findByRole('article', { name: 'Submission Test Student One' }),
    ).toBeVisible()
    expect(screen.queryByText('Drag & Drop Scanned Student Answer Sheets')).toBeNull()
    // Every state: a submitted or frozen unit stays readable (P28).
    expect(mock.callsTo('GET /api/v1/units')[0]?.query.get('state')).toBeNull()
    expect(mock.callsTo('GET /api/v1/booklets')[0]?.query.get('unit_id')).toBe(UNIT)
  })

  it('lists the booklets of the unit picked', async () => {
    const mock = signedIn({
      'GET /api/v1/booklets': (call) =>
        call.query.get('unit_id') === 'unit-2'
          ? listOf([
              booklet({
                id: 'b-2',
                student: { id: 's-2', name: 'Test Student Two', usn: 'TST002' },
                unit: { id: 'unit-2', code: 'U-0002' },
              }),
            ])
          : listOf(),
    })
    const user = userEvent.setup()
    renderApp('/evaluate')
    await screen.findByRole('article', { name: 'Submission Test Student One' })
    await user.selectOptions(screen.getByLabelText('Unit'), 'unit-2')
    expect(
      await screen.findByRole('article', { name: 'Submission Test Student Two' }),
    ).toBeVisible()
    expect(screen.queryByRole('article', { name: 'Submission Test Student One' })).toBeNull()
    expect(mock.callsTo('GET /api/v1/booklets').at(-1)?.query.get('unit_id')).toBe('unit-2')
  })

  it('lists the submissions with name, USN, pages, status and result, and the right button', async () => {
    signedIn({
      'GET /api/v1/booklets': listOf([
        booklet({ id: 'b-ready', status: 'scored' }),
        booklet({
          id: 'b-working',
          status: 'reading',
          student: { id: 's-2', name: 'Test Student Two', usn: 'TST002' },
          pages_read: 1,
        }),
        booklet({
          id: 'b-done',
          status: 'approved',
          student: { id: 's-3', name: 'Test Student Three', usn: 'TST003' },
          result: { total: 4.5, max_marks: 6, sheet_version: 1 },
        }),
        booklet({
          id: 'b-failed',
          status: 'failed',
          failure_reason: 'unreadable_file',
          student: { id: 's-4', name: 'Test Student Four', usn: 'TST004' },
          page_count: 0,
        }),
        booklet({
          id: 'b-retake',
          status: 'needs_retake',
          flagged_pages: [2],
          student: { id: 's-5', name: 'Test Student Five', usn: 'TST005' },
        }),
      ]),
    })
    renderApp('/evaluate')
    const ready = await screen.findByRole('article', { name: 'Submission Test Student One' })
    expect(within(ready).getByText('USN: TST001')).toBeVisible()
    expect(within(ready).getByText('Ready for review')).toBeVisible()
    expect(within(ready).getByText('2')).toBeVisible()
    expect(within(ready).getByText('Awaiting approval')).toBeVisible()
    expect(within(ready).getByRole('button', { name: 'Open' })).toBeVisible()

    const working = screen.getByRole('article', { name: 'Submission Test Student Two' })
    expect(within(working).getByText('Reading handwriting')).toBeVisible()
    expect(within(working).getByRole('button', { name: 'Run AI Eval' })).toBeVisible()

    const done = screen.getByRole('article', { name: 'Submission Test Student Three' })
    expect(within(done).getByText('4.5 / 6')).toBeVisible()

    const failed = screen.getByRole('article', { name: 'Submission Test Student Four' })
    expect(within(failed).getByRole('alert')).toHaveTextContent('The file could not be read.')
    expect(within(failed).queryByRole('button', { name: /Open|Run AI Eval/ })).toBeNull()

    // Retakes are the admin's (P26): the SME is told, and offered no page decisions.
    const retake = screen.getByRole('article', { name: 'Submission Test Student Five' })
    expect(within(retake).getByText('Waiting for a retake by your admin')).toBeVisible()
    expect(within(retake).queryByRole('button', { name: /anyway|flagged/ })).toBeNull()
    expect(screen.getByText(/Student Answer Submissions \(5\)/)).toBeVisible()
  })

  it('deletes a booklet after asking', async () => {
    const mock = signedIn({ [`DELETE ${B}`]: { status: 204 } })
    const user = userEvent.setup()
    renderApp('/evaluate')
    await user.click(
      await screen.findByRole('button', { name: 'Delete the booklet of Test Student One' }),
    )
    expect(mock.callsTo(`DELETE ${B}`)).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(mock.callsTo(`DELETE ${B}`)).toHaveLength(1))
  })

  it('explains an empty unit', async () => {
    signedIn({ 'GET /api/v1/booklets': listOf([]) })
    renderApp('/evaluate')
    expect(await screen.findByText('No submissions yet')).toBeVisible()
  })

  it('says so when no unit is assigned to me', async () => {
    const mock = signedIn({ 'GET /api/v1/units': { json: [] } })
    renderApp('/evaluate')
    expect(await screen.findByText('No units assigned to you')).toBeVisible()
    expect(mock.callsTo('GET /api/v1/booklets')).toHaveLength(0)
  })

  it('asks the admin to move my unit, with a reason, and lists my requests', async () => {
    let asked = false
    const mine = requestOf({ id: 'req-9', reason: 'Exams clash' })
    const mock = signedIn({
      'GET /api/v1/units': () => ({
        json: asked
          ? [{ ...UNITS[0]!, pending_request: { id: 'req-9', requested_at: mine.requested_at } }]
          : UNITS,
      }),
      'GET /api/v1/delegation-requests': () => ({ json: asked ? [mine] : [] }),
      [`POST /api/v1/units/${UNIT}/delegation-requests`]: () => {
        asked = true
        return { status: 201, json: mine }
      },
      'POST /api/v1/delegation-requests/req-9/withdraw': () => {
        asked = false
        return { json: { ...mine, state: 'withdrawn' } }
      },
    })
    const user = userEvent.setup()
    renderApp('/evaluate')
    await user.click(await screen.findByRole('button', { name: 'Request reassignment' }))
    const dialog = await screen.findByRole('dialog')
    const send = within(dialog).getByRole('button', { name: 'Send request' })
    expect(send).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Reason'), 'Exams clash')
    await user.click(send)
    expect(await screen.findByText(/your admin decides who takes U-0001/)).toBeVisible()
    expect(mock.callsTo(`POST /api/v1/units/${UNIT}/delegation-requests`)[0]?.body).toEqual({
      reason: 'Exams clash',
    })
    expect(await screen.findByText('Reassignment requested')).toBeVisible()
    const list = await screen.findByRole('region', { name: 'My reassignment requests' })
    expect(within(list).getByText('Pending')).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Withdraw request' }))
    expect(await screen.findByText('Request for U-0001 withdrawn.')).toBeVisible()
    expect(await screen.findByRole('button', { name: 'Request reassignment' })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------------------------
// Step 2: AI Segmentation
// ---------------------------------------------------------------------------------------------

type Routes = Parameters<typeof mockApi>[0]

/** A scored booklet open for review: the routes the screen reads. */
function segmentation(extra: Routes = {}, initial = review()) {
  return signedIn({
    [`GET ${B}`]: { json: detail() },
    [`POST ${B}/lock`]: { json: initial },
    [`GET ${B}/review`]: { json: initial },
    [`GET ${B}/segments`]: { json: SEGMENTS },
    [`GET ${B}/pages/1/text`]: { json: PAGE_TEXT[1] },
    [`GET ${B}/pages/2/text`]: { json: PAGE_TEXT[2] },
    [`GET ${B}/pages/1/image`]: PNG,
    [`GET ${B}/pages/2/image`]: PNG,
    [`GET ${B}/diagrams`]: { json: [] },
    [`DELETE ${B}/lock`]: { status: 204 },
    [`GET /api/v1/blueprints/${BLUEPRINT}`]: {
      json: {
        ...exam,
        document: {
          sections: [
            { items: [{ label: '1' }, { label: '2' }, { label: '3', parts: [{ label: 'a' }] }] },
          ],
        },
      },
    },
    ...extra,
  })
}

async function openSegments(extra: Routes = {}, initial = review()) {
  const mock = segmentation(extra, initial)
  const user = userEvent.setup()
  renderApp(`/evaluate?booklet=${BOOKLET}`)
  await screen.findByRole('article', { name: /Question 1 Answer Clip/ })
  return { mock, user }
}

const lastBody = (mock: ReturnType<typeof mockApi>, key: string) =>
  mock.callsTo(key).at(-1)?.body as Record<string, unknown>

describe('step 2: AI segmentation', () => {
  it('takes the lock, shows a box per answer on the page and a card per segment', async () => {
    const { mock } = await openSegments()
    expect(mock.callsTo(`POST ${B}/lock`)).toHaveLength(1)
    expect(await screen.findByText(/You have this booklet open/)).toBeVisible()
    expect(
      screen.getByRole('heading', { name: 'AI Cropped & OCR Extracted Segments' }),
    ).toBeVisible()

    // boxes on page 1 for the two answers; the stray text is on page 2
    expect(await screen.findByRole('button', { name: 'Answer 1 on page 1' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Answer 2 on page 1' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Unassigned text on page 1/ })).toBeNull()

    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    // the text reads as one paragraph, with no confidence figures for the teacher
    expect(within(one).getByLabelText('Text of Question 1 Answer Clip')).toHaveTextContent(
      'Lenz law opposes the change the emf is induced',
    )
    expect(one).not.toHaveTextContent(/OCR confidence|\d+%/)
    expect(within(one).getByText('Suggested 2 / 3')).toBeVisible()
    expect(within(one).getByText('1 line to check')).toBeVisible()
    expect(
      within(one).getByRole('button', { name: 'Line 2, low OCR confidence: the emf is induced' }),
    ).toBeVisible()
    // the unsure line is also outlined on the page
    expect(screen.getAllByTestId('unsure-line')).toHaveLength(1)

    const tray = screen.getByRole('region', { name: 'Unassigned tray' })
    expect(within(tray).getByText('Unassigned tray (1)')).toBeVisible()
    expect(within(tray).getByText('stray words')).toBeVisible()
  })

  it("shows the official question and folds the student's copy of it", async () => {
    const { user } = await openSegments({
      [`GET /api/v1/blueprints/${BLUEPRINT}`]: {
        json: {
          ...exam,
          document: {
            sections: [
              {
                items: [
                  { label: '1', question_id: 'q-1' },
                  { label: '2' },
                  { label: '3', parts: [{ label: 'a' }] },
                ],
              },
            ],
          },
        },
      },
      'GET /api/v1/questions/q-1': {
        json: qnaDetail({ id: 'q-1', text: 'Lenz law: what opposes the change of flux?' }),
      },
    })
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    const asked = await within(one).findByRole('region', { name: 'Question 1' })
    expect(asked).toHaveTextContent('Lenz law: what opposes the change of flux?')
    const answerText = within(one).getByLabelText('Text of Question 1 Answer Clip')
    expect(answerText).toHaveTextContent('the emf is induced')
    expect(answerText).not.toHaveTextContent('Lenz law opposes the change')
    const fold = within(asked).getByRole('button', { name: /copy of the question \(1 line\)/ })
    expect(within(one).queryByRole('button', { name: /Line 1/ })).toBeNull()
    await user.click(fold)
    // the copied line can still be shown on the page and edited
    await user.click(within(asked).getByRole('button', { name: /Line 1/ }))
    expect(screen.getByTestId('picked-line')).toBeInTheDocument()
    expect(within(one).getByRole('textbox', { name: 'Correct the text of this line' })).toHaveValue(
      'Lenz law opposes the change',
    )
  })

  it('shows a line picked in the text on its own page', async () => {
    const { user } = await openSegments()
    expect(screen.queryByTestId('picked-line')).toBeNull()
    const tray = screen.getByRole('region', { name: 'Unassigned tray' })
    // the stray text is on page 2: picking it turns the page and outlines the line
    await user.click(within(tray).getByRole('button', { name: /stray words/ }))
    expect(screen.getByText('Page 2 of 2')).toBeVisible()
    expect(screen.getByTestId('picked-line')).toBeInTheDocument()
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    await user.click(within(one).getByRole('button', { name: /Line 2/ }))
    expect(screen.getByText('Page 1 of 2')).toBeVisible()
    expect(screen.getByTestId('picked-line')).toBeInTheDocument()
  })

  it('shows the scanner beam while the machine is working, and no edit tools', async () => {
    signedIn({
      [`GET ${B}`]: {
        json: detail({
          status: 'reading',
          pages_read: 1,
          pages: [page(1), page(2, { text_read: false, text_url: null })],
        }),
      },
      [`GET ${B}/pages/1/text`]: { json: PAGE_TEXT[1] },
      [`GET ${B}/pages/1/image`]: PNG,
      [`GET /api/v1/blueprints/${BLUEPRINT}`]: { json: { ...exam, document: {} } },
    })
    renderApp(`/evaluate?booklet=${BOOKLET}`)
    expect(await screen.findByText('Reading handwriting: 1 of 2 pages.')).toBeVisible()
    expect(screen.getByTestId('scan-beam')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Proceed to Evaluation' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Re-Segment' })).toBeDisabled()
  })

  it('edits a line, saves it, and shows the re-score as it arrives', async () => {
    let phase: 'before' | 'pending' | 'after' = 'before'
    const views = {
      before: review(),
      pending: review({
        answers: [
          answer('a1', '1', 2, { rescore_pending: true, version: 3 }),
          answer('a2', '2', 1),
        ],
      }),
      after: review({
        version: 6,
        answers: [answer('a1', '1', 3, { version: 4 }), answer('a2', '2', 1)],
      }),
    }
    const { mock, user } = await openSegments({
      [`GET ${B}/review`]: () => ({ json: views[phase] }),
      [`POST ${B}/regions/r2`]: () => {
        phase = 'pending'
        setTimeout(() => (phase = 'after'), 1700)
        return {
          json: {
            booklet_version: 6,
            region: PAGE_TEXT[1]?.regions[1],
            rescoring: ['a1'],
          },
        }
      },
    })
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    await user.click(
      within(one).getByRole('button', { name: 'Line 2, low OCR confidence: the emf is induced' }),
    )
    const box = within(one).getByRole('textbox', { name: 'Correct the text of this line' })
    expect(box).toHaveValue('the emf is induced')
    expect(
      within(one).getByText(/saved with the booklet for the OCR accuracy benchmark/),
    ).toBeVisible()
    expect(within(one).getByRole('button', { name: 'Save text' })).toBeDisabled()
    await user.clear(box)
    await user.type(box, 'the emf is induced in the coil')
    await user.click(within(one).getByRole('button', { name: 'Save text' }))

    expect(
      await screen.findByText(
        /Line saved and kept for the OCR benchmark\. Re-scoring question 1\./,
      ),
    ).toBeVisible()
    expect(lastBody(mock, `POST ${B}/regions/r2`)).toEqual({
      expected_version: 5,
      text: 'the emf is induced in the coil',
    })
    const results = await screen.findByRole('region', { name: 'Re-score results' })
    expect(await within(results).findByText(/Re-scoring…/)).toBeVisible()
    expect(screen.getByTestId('scan-beam')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Proceed to Evaluation' })).toBeDisabled()

    await waitFor(() => expect(within(results).getByText(/new suggestion/)).toBeVisible(), {
      timeout: 5000,
    })
    expect(within(results).getByText('3 / 3')).toBeVisible()
    expect(within(results).getByText('(was 2)')).toBeVisible()
    await waitFor(() => expect(screen.queryByTestId('scan-beam')).toBeNull())
    expect(screen.getByRole('button', { name: 'Proceed to Evaluation' })).toBeEnabled()
  })

  it('marks a line as struck out without touching its text', async () => {
    const { mock, user } = await openSegments({
      [`POST ${B}/regions/r1`]: {
        json: { booklet_version: 6, region: PAGE_TEXT[1]?.regions[0], rescoring: [] },
      },
    })
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    await user.click(
      within(one).getByRole('button', { name: 'Line 1: Lenz law opposes the change' }),
    )
    await user.click(within(one).getByRole('checkbox', { name: /struck this line out/ }))
    await waitFor(() =>
      expect(lastBody(mock, `POST ${B}/regions/r1`)).toEqual({
        expected_version: 5,
        struck_out: true,
      }),
    )
    expect(await screen.findByText(/Line left out of scoring/)).toBeVisible()
  })

  it('merges, reassigns, splits and moves a boundary, each as one call with the version', async () => {
    // Every edit moves the booklet's version on, as the server does.
    let version = 5
    const done =
      (rescoring: string[] = []) =>
      () => {
        version++
        return { json: { ...SEGMENTS, booklet_version: version, rescoring } }
      }
    const { mock, user } = await openSegments({
      [`GET ${B}/review`]: () => ({ json: review({ version }) }),
      [`POST ${B}/segments/merge`]: done(['a1']),
      [`POST ${B}/segments/reassign`]: done(['a2']),
      [`POST ${B}/segments/split`]: done(),
      [`POST ${B}/segments/move-boundary`]: done(['a1', 'a2']),
    })
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    await user.click(within(one).getByRole('button', { name: 'Merge with next segment' }))
    expect(await screen.findByText(/Segments merged\. Re-scoring question 1\./)).toBeVisible()
    expect(lastBody(mock, `POST ${B}/segments/merge`)).toEqual({
      expected_version: 5,
      first: 's1',
      second: 's2',
    })

    const tray = screen.getByRole('region', { name: 'Unassigned tray' })
    await user.selectOptions(
      within(tray).getByRole('combobox', { name: 'Reassign Unassigned text' }),
      '3.a',
    )
    await waitFor(() =>
      expect(lastBody(mock, `POST ${B}/segments/reassign`)).toEqual({
        expected_version: 6,
        segment_id: 's3',
        label: '3.a',
      }),
    )

    await user.click(
      within(one).getByRole('button', { name: 'Line 2, low OCR confidence: the emf is induced' }),
    )
    await user.click(within(one).getByRole('button', { name: 'Split here' }))
    await waitFor(() =>
      expect(lastBody(mock, `POST ${B}/segments/split`)).toEqual({
        expected_version: 7,
        segment_id: 's1',
        at_region: 'r2',
        label: null,
      }),
    )

    await user.click(
      within(one).getByRole('button', { name: 'Line 2, low OCR confidence: the emf is induced' }),
    )
    await user.click(
      within(one).getByRole('button', { name: 'Move this line and the rest to the next segment' }),
    )
    await waitFor(() =>
      expect(lastBody(mock, `POST ${B}/segments/move-boundary`)).toEqual({
        expected_version: 8,
        upper: 's1',
        lower: 's2',
        region: 'r2',
      }),
    )
  })

  it('cannot split at the first line or move a boundary where there is no neighbour', async () => {
    const { user } = await openSegments()
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    await user.click(
      within(one).getByRole('button', { name: 'Line 1: Lenz law opposes the change' }),
    )
    expect(within(one).getByRole('button', { name: 'Split here' })).toBeDisabled()
    const tray = screen.getByRole('region', { name: 'Unassigned tray' })
    // the last segment has nothing after it to merge with
    expect(within(tray).getByRole('button', { name: 'Merge with next segment' })).toBeDisabled()
  })

  it('segments again after asking, and says when the worker is done', async () => {
    let version = 5
    const { mock, user } = await openSegments({
      [`GET ${B}/review`]: () => ({ json: review({ version }) }),
      [`POST ${B}/segments/resegment`]: () => {
        setTimeout(() => (version = 6), 1700)
        return { status: 202, json: { booklet_version: 5 } }
      },
    })
    await user.click(screen.getByRole('button', { name: 'Re-Segment' }))
    const ask = screen.getByRole('alertdialog', { name: 'Segment again' })
    expect(ask).toHaveTextContent('replaces every segment, including the ones you changed')
    expect(mock.callsTo(`POST ${B}/segments/resegment`)).toHaveLength(0)
    await user.click(within(ask).getByRole('button', { name: 'Yes, segment again' }))
    expect(lastBody(mock, `POST ${B}/segments/resegment`)).toEqual({ expected_version: 5 })
    expect(await screen.findByText('Segmenting again with your corrected text…')).toBeVisible()
    expect(screen.getByTestId('scan-beam')).toBeVisible()
    expect(
      await screen.findByText(
        'The booklet was segmented again. Check the new split.',
        {},
        { timeout: 5000 },
      ),
    ).toBeVisible()
    await waitFor(() => expect(screen.queryByTestId('scan-beam')).toBeNull())
  })

  it('does not segment again when Cancel is pressed', async () => {
    const { mock, user } = await openSegments()
    await user.click(screen.getByRole('button', { name: 'Re-Segment' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(mock.callsTo(`POST ${B}/segments/resegment`)).toHaveLength(0)
  })

  it('is read-only while another teacher has the booklet open', async () => {
    const held = review({
      lock: {
        holder_id: 'u-2',
        holder_name: 'Dr. Other',
        acquired_at: '2026-10-06T08:10:00Z',
        expires_at: '2026-10-06T08:25:00Z',
        mine: false,
      },
    })
    const { user } = await openSegments(
      {
        [`POST ${B}/lock`]: {
          status: 423,
          json: {
            detail: 'Open by someone else.',
            holder_id: 'u-2',
            expires_at: '2026-10-06T08:25:00Z',
          },
        },
      },
      held,
    )
    expect(await screen.findByText(/Dr\. Other has this booklet open/)).toBeVisible()
    expect(screen.getByText(/You can look, but not change anything/)).toBeVisible()
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    // a line can still be shown on the page, but not edited
    await user.click(within(one).getByRole('button', { name: /Line 1/ }))
    expect(screen.getByTestId('picked-line')).toBeInTheDocument()
    expect(within(one).queryByRole('textbox')).toBeNull()
    expect(within(one).queryByRole('button', { name: 'Merge with next segment' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Re-Segment' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Proceed to Evaluation' }))
    // step 3 opens read-only too, with the holder named
    const grading = await screen.findByRole('region', { name: 'Panel C: SME Final Grading' })
    expect(within(grading).getByRole('button', { name: 'Approve answer' })).toBeDisabled()
    expect(screen.getAllByText(/Dr\. Other has this booklet open/).length).toBeGreaterThan(0)
  })

  it('reloads and says so when the booklet changed since the screen was loaded', async () => {
    const { mock, user } = await openSegments({
      [`POST ${B}/segments/merge`]: {
        status: 409,
        json: { detail: 'The booklet changed since you opened it.' },
      },
    })
    const reviews = mock.callsTo(`GET ${B}/review`).length
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    await user.click(within(one).getByRole('button', { name: 'Merge with next segment' }))
    expect(
      await screen.findByText(/The booklet changed since you opened it\. The screen was reloaded/),
    ).toBeVisible()
    await waitFor(() => expect(mock.callsTo(`GET ${B}/review`).length).toBeGreaterThan(reviews))
  })

  it('opens the lock again when it was lost (423 on a write)', async () => {
    const { mock, user } = await openSegments({
      [`POST ${B}/segments/merge`]: {
        status: 423,
        json: { detail: 'Not open.', holder_id: null, expires_at: null },
      },
    })
    const one = screen.getByRole('article', { name: /Question 1 Answer Clip/ })
    await user.click(within(one).getByRole('button', { name: 'Merge with next segment' }))
    expect(await screen.findByText(/You no longer have this booklet open/)).toBeVisible()
    await waitFor(() => expect(mock.callsTo(`POST ${B}/lock`)).toHaveLength(2))
  })

  it('leaves an approved booklet read-only', async () => {
    const approvedReview = review({ status: 'approved', approved: true })
    signedIn({
      [`GET ${B}`]: { json: detail({ status: 'approved' }) },
      [`POST ${B}/lock`]: { json: approvedReview },
      [`GET ${B}/review`]: { json: approvedReview },
      [`GET ${B}/segments`]: { json: SEGMENTS },
      [`GET ${B}/pages/1/text`]: { json: PAGE_TEXT[1] },
      [`GET ${B}/pages/2/text`]: { json: PAGE_TEXT[2] },
      [`GET ${B}/pages/1/image`]: PNG,
      [`GET ${B}/diagrams`]: { json: [] },
      [`DELETE ${B}/lock`]: { status: 204 },
      [`GET /api/v1/blueprints/${BLUEPRINT}`]: { json: { ...exam, document: {} } },
    })
    renderApp(`/evaluate?booklet=${BOOKLET}`)
    expect(
      await screen.findByText(/This booklet is approved\. Segments cannot be changed here/),
    ).toBeVisible()
    const one = await screen.findByRole('article', { name: /Question 1 Answer Clip/ })
    expect(within(one).getByRole('button', { name: /Line 1/ })).toBeVisible()
    expect(within(one).queryByRole('textbox')).toBeNull()
  })

  describe('drawings', () => {
    const drawing = {
      id: 'd-1',
      segment_id: 's2',
      region_id: 'r6',
      version: 3,
      kind: 'flowchart' as const,
      box: [100, 600, 700, 1000],
      graph: graph(),
    }
    const withDrawing = {
      ...PAGE_TEXT[1]!,
      regions: [
        ...PAGE_TEXT[1]!.regions,
        region('r6', [100, 600, 700, 1000], '', { kind: 'diagram', text: null }),
      ],
    }

    it('shows each drawing over its page and sends a correction with the drawing version', async () => {
      const { mock, user } = await openSegments({
        [`GET ${B}/diagrams`]: { json: [drawing] },
        [`GET ${B}/pages/1/text`]: { json: withDrawing },
        [`POST ${B}/diagrams/d-1/edits`]: { json: { ...drawing, version: 4 } },
      })
      const editor = await screen.findByRole('region', { name: 'Diagram in question 2' })
      expect(within(editor).getByRole('group', { name: /over the drawing/ })).toHaveAttribute(
        'viewBox',
        '100 600 600 400',
      )
      await user.click(within(editor).getByRole('button', { name: 'Node Add' }))
      await user.selectOptions(within(editor).getByRole('combobox', { name: 'Shape' }), 'io')
      await waitFor(() =>
        expect(lastBody(mock, `POST ${B}/diagrams/d-1/edits`)).toEqual({
          expected_version: 3,
          edits: [{ op: 'reshape_node', id: 'n2', shape: 'io' }],
        }),
      )
    })

    it('cannot be edited while another teacher has the booklet', async () => {
      signedIn({
        [`GET ${B}`]: { json: detail() },
        [`POST ${B}/lock`]: {
          status: 423,
          json: { detail: 'Open.', holder_id: 'u-2', expires_at: null },
        },
        [`GET ${B}/review`]: {
          json: review({
            lock: {
              holder_id: 'u-2',
              holder_name: 'Dr. Other',
              acquired_at: '2026-10-06T08:10:00Z',
              expires_at: '2026-10-06T08:25:00Z',
              mine: false,
            },
          }),
        },
        [`GET ${B}/segments`]: { json: SEGMENTS },
        [`GET ${B}/pages/1/text`]: { json: withDrawing },
        [`GET ${B}/pages/2/text`]: { json: PAGE_TEXT[2] },
        [`GET ${B}/pages/1/image`]: PNG,
        [`GET ${B}/pages/2/image`]: PNG,
        [`GET ${B}/diagrams`]: { json: [drawing] },
        [`GET /api/v1/blueprints/${BLUEPRINT}`]: { json: { ...exam, document: {} } },
      })
      renderApp(`/evaluate?booklet=${BOOKLET}`)
      const editor = await screen.findByRole('region', { name: 'Diagram in question 2' })
      expect(within(editor).getByRole('note')).toHaveTextContent('Dr. Other has this booklet open')
      expect(within(editor).getByRole('button', { name: 'Add at centre' })).toBeDisabled()
    })
  })

  it('goes back to the submissions, and a stepper click to step 1 does too', async () => {
    const { user } = await openSegments()
    await user.click(screen.getByRole('button', { name: /Submissions/ }))
    expect(await screen.findByText(/Student Answer Submissions/)).toBeVisible()
  })

  it('asks for a booklet when step 2 is opened without one', async () => {
    signedIn()
    const user = userEvent.setup()
    renderApp('/evaluate')
    await user.click(await screen.findByRole('button', { name: '2. AI Segmentation' }))
    expect(await screen.findByText('Choose a booklet first')).toBeVisible()
  })

  it('says so for a booklet that does not exist', async () => {
    signedIn({ [`GET ${B}`]: { status: 404, json: { detail: 'Not found.' } } })
    renderApp(`/evaluate?booklet=${BOOKLET}`)
    expect(await screen.findByText('This booklet could not be opened')).toBeVisible()
  })

  it('releases the lock on leaving the screen', async () => {
    const { mock, user } = await openSegments()
    await user.click(screen.getByRole('button', { name: /Submissions/ }))
    await waitFor(() => expect(mock.callsTo(`DELETE ${B}/lock`)).toHaveLength(1), { timeout: 2000 })
  })
})
