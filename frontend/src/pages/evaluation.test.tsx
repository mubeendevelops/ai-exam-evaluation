import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { components } from '../api/schema'
import { healthy, meOf, tokenOf } from '../test/fixtures'
import {
  BLUEPRINT,
  BOOKLET,
  PAGE_TEXT,
  SEGMENTS,
  answer,
  detail,
  graph,
  review,
} from '../test/evaluateFixtures'
import { mockApi } from '../test/mockApi'
import { detail as questionDetail } from '../test/qnaFixtures'
import { renderApp } from '../test/render'

type Review = components['schemas']['ReviewOut']
type Answer = components['schemas']['ReviewAnswerOut']
type Sheet = components['schemas']['ResultSheetOut']
type Routes = Parameters<typeof mockApi>[0]

const B = `/api/v1/booklets/${BOOKLET}`
const PNG = {
  body: new Blob(['x'], { type: 'image/png' }),
  headers: { 'Content-Type': 'image/png' },
}

beforeEach(() => {
  Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  localStorage.clear()
})
afterEach(() => {
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
  owning_college_id: 'c-1',
  owned: true,
  copied_from: null,
}

const document = {
  sections: [
    {
      label: 'A',
      items: [
        { type: 'question', label: '1', marks: 3, question_id: 'q-1' },
        { type: 'question', label: '2', marks: 3, question_id: 'q-2' },
        { type: 'question', label: '3', marks: 3, question_id: null },
      ],
    },
  ],
}

const criterion = (id: string, marks: number, credit: number, over = {}) => ({
  criterion_id: id,
  criterion_version: 1,
  weight: 1.5,
  credit,
  marks,
  scorer: 'list-v1',
  flags: [],
  similarity: null,
  reason: null,
  matched: [],
  missing: [],
  ...over,
})

function scored(id: string, label: string, mark: number, over: Partial<Answer> = {}): Answer {
  const base = answer(id, label, mark, over)
  return {
    ...base,
    suggestion: base.suggestion && {
      ...base.suggestion,
      criteria: [
        criterion('c-1', 1.5, 1, {
          reason: 'Names the effect.',
          matched: ['opposes'],
          missing: ['resists'],
        }),
        criterion('c-2', mark - 1.5, 0.5, {
          scorer: 'semantic-v1',
          similarity: 0.61,
          reason: 'Explains the cause only in part.',
          flags: ['check'],
        }),
      ],
      ...over.suggestion,
    },
  }
}

const sheet = (version: number, total: number): Sheet => ({
  id: `sheet-${version}`,
  version,
  total,
  max_marks: 6,
  issued_by: 'u-1',
  issued_at: '2026-10-06T09:00:00Z',
  note: version > 1 ? 'Amended: question 1' : '',
  pdf_url: `/api/v1/booklets/b-1/result-sheets/${version}/pdf`,
  lines: [],
})

const slots = (counted2 = true): Review['totals']['slots'] => [
  { section_label: 'A', slot_label: '1', mark: 2, counted: true, outcome: 'counted' },
  {
    section_label: 'A',
    slot_label: '2',
    mark: 1,
    counted: counted2,
    outcome: counted2 ? 'counted' : 'not counted: best N',
  },
  {
    section_label: 'A',
    slot_label: '3',
    mark: null,
    counted: false,
    outcome: 'not attempted',
  },
]

function start(over: Partial<Review> = {}): Review {
  return review({
    answers: [
      scored('a1', '1', 2),
      scored('a2', '2', 1),
      answer('a3', '3', null, { attempted: false }),
    ],
    totals: { total: 3, max_marks: 6, slots: slots() },
    ...over,
  })
}

const base = (extra: Routes = {}): Routes => ({
  'POST /api/v1/auth/refresh': { json: tokenOf() },
  'GET /api/v1/auth/me': { json: meOf() },
  'GET /api/v1/health': { json: healthy },
  [`GET ${B}`]: { json: detail({ status: 'in_review' }) },
  [`GET ${B}/segments`]: { json: SEGMENTS },
  [`GET ${B}/pages/1/text`]: { json: PAGE_TEXT[1] },
  [`GET ${B}/pages/2/text`]: { json: PAGE_TEXT[2] },
  [`GET ${B}/pages/1/image`]: PNG,
  [`GET ${B}/pages/2/image`]: PNG,
  [`GET ${B}/diagrams`]: { json: [] },
  [`DELETE ${B}/lock`]: { status: 204 },
  [`GET /api/v1/blueprints/${BLUEPRINT}`]: { json: { ...exam, document } },
  'GET /api/v1/questions/q-1': { json: questionDetail() },
  'GET /api/v1/questions/q-2': {
    json: questionDetail({ id: 'q-2', code: 'PHY-Q2', text: 'Define resonance.' }),
  },
  [`GET ${B}/answers/a1/diagram-comparisons`]: { json: [] },
  [`GET ${B}/answers/a2/diagram-comparisons`]: { json: [] },
  ...extra,
})

/**
 * A small stand-in for the review endpoints: the decisions change one review object and answer
 * with all of it, like the API does.
 */
function server(initial: Review, extra: Routes = {}) {
  const state = { review: initial }
  const nextVersion = () => {
    state.review = { ...state.review, version: state.review.version + 1 }
  }
  const change = (id: string, fn: (a: Answer) => Answer) => {
    state.review = {
      ...state.review,
      answers: state.review.answers.map((a) =>
        a.id === id ? fn({ ...a, version: a.version + 1 }) : a,
      ),
    }
    const open = state.review.answers.filter((a) => a.attempted)
    state.review.can_approve = open.every((a) => a.status === 'approved')
  }
  const routes: Routes = {
    [`POST ${B}/lock`]: () => ({ json: state.review }),
    [`GET ${B}/review`]: () => ({ json: state.review }),
    [`POST ${B}/approve`]: () => {
      state.review = {
        ...state.review,
        status: 'approved',
        approved: true,
        can_approve: false,
        sheets: [sheet(1, state.review.totals.total)],
      }
      nextVersion()
      return { json: state.review }
    },
  }
  for (const id of ['a1', 'a2']) {
    routes[`POST ${B}/answers/${id}/approve`] = (call) => {
      const body = call.body as { teacher_mark: number | null; tags: string[]; remarks: string }
      change(id, (a) => {
        const ai = a.suggestion?.mark ?? null
        const mark = body.teacher_mark ?? ai ?? 0
        return {
          ...a,
          status: 'approved',
          draft: null,
          approval: {
            id: `rv-${id}`,
            ai_mark: ai,
            teacher_mark: mark,
            overridden: body.teacher_mark !== null && body.teacher_mark !== ai,
            tags: body.tags,
            remarks: body.remarks,
            reviewer_id: 'u-1',
            reviewed_at: '2026-10-06T08:30:00Z',
          },
        }
      })
      if (state.review.amendment_in_progress && state.review.answers.every((a) => !a.draft)) {
        state.review = {
          ...state.review,
          status: 'approved_amended',
          amendment_in_progress: false,
          sheets: [...state.review.sheets, sheet(2, state.review.totals.total)],
        }
      }
      nextVersion()
      return { json: state.review }
    }
    routes[`POST ${B}/answers/${id}/skip`] = () => {
      change(id, (a) => ({ ...a, status: 'skipped' }))
      return { json: state.review }
    }
    routes[`POST ${B}/answers/${id}/reopen`] = (call) => {
      const reason = (call.body as { reason: string }).reason
      const afterApproval = state.review.approved
      change(id, (a) => ({
        ...a,
        status: 'suggested',
        approval: afterApproval ? a.approval : null,
        draft: afterApproval
          ? { amendment_id: 'am-1', reason, opened_by: 'u-1', opened_at: '2026-10-06T09:30:00Z' }
          : null,
      }))
      if (afterApproval) {
        state.review = {
          ...state.review,
          status: 'amendment_in_progress',
          amendment_in_progress: true,
        }
      }
      return { json: state.review }
    }
    routes[`POST ${B}/answers/${id}/withdraw`] = () => {
      change(id, (a) => ({ ...a, status: 'approved', draft: null }))
      state.review = { ...state.review, status: 'approved', amendment_in_progress: false }
      return { json: state.review }
    }
  }
  const mock = mockApi(base({ ...routes, ...extra }))
  return { mock, state }
}

async function open(initial: Review, extra: Routes = {}, query = '') {
  const s = server(initial, extra)
  const user = userEvent.setup()
  const view = renderApp(`/evaluate?booklet=${BOOKLET}&step=3${query}`)
  await screen.findByRole('region', { name: 'Panel C: Teacher Final Grading' })
  return { ...s, user, view }
}

const lastBody = (mock: ReturnType<typeof mockApi>, key: string) =>
  mock.callsTo(key).at(-1)?.body as Record<string, unknown>

describe('step 3: evaluation view', () => {
  it('shows the header and the three panels for the first answer', async () => {
    await open(start())
    expect(screen.getByText(/Evaluating Student:/)).toHaveTextContent(
      'Test Student One (USN: TST001)',
    )
    expect(screen.getByRole('button', { name: 'Back to Segments' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save & Export Grade Sheet' })).toBeVisible()
    expect(await screen.findByText(/You have this booklet open\./)).toBeVisible()

    const a = screen.getByRole('region', { name: 'Panel A: Answer Mapping' })
    expect(await within(a).findByText('State Lenz’s law.')).toBeVisible()
    const key = within(a).getByRole('region', { name: 'Reference key' })
    expect(key).toHaveTextContent('The induced emf opposes the change of flux.')
    const text = within(a).getByRole('region', { name: "Student's answer" })
    expect(text).toHaveTextContent('Lenz law opposes the change')
    expect(text).toHaveTextContent('the emf is induced')
    expect(within(text).getByTitle(/Low OCR confidence/)).toHaveTextContent('the emf is induced')
    expect(a).toHaveTextContent('Question match: placed by the question number written')
    const items = within(a).getByRole('region', { name: 'Keywords and items matched' })
    expect(items).toHaveTextContent('1 of 2')
    expect(within(items).getByText('Found:')).toBeInTheDocument()
    expect(within(items).getByText('Missing:')).toBeInTheDocument()

    const b = screen.getByRole('region', { name: 'Panel B: AI Diagnostic Reasoning' })
    expect(within(b).getByText('Auto-Suggested')).toBeVisible()
    expect(within(b).getByText('Suggested AI Marks: 2 / 3')).toBeVisible()
    expect(within(b).getByText('80%')).toBeVisible()
    expect(within(b).getByText('65%')).toBeVisible()
    const criteria = within(b).getByRole('list', { name: 'Rubric criteria' })
    expect(within(criteria).getByText('Names the effect')).toBeVisible()
    expect(within(criteria).getByText('Explains why')).toBeVisible()
    expect(within(criteria).getByText('Explains the cause only in part.')).toBeVisible()
    expect(within(criteria).getByText(/Borderline/)).toBeVisible()
    expect(
      within(criteria).getByRole('progressbar', { name: 'Names the effect: 100% credit' }),
    ).toBeVisible()

    const c = screen.getByRole('region', { name: 'Panel C: Teacher Final Grading' })
    expect(within(c).getByText('Human In The Loop')).toBeVisible()
    expect(within(c).getByRole('button', { name: 'Approve answer' })).toBeEnabled()
    expect(screen.queryByText('Complete Evaluation for Q1')).toBeNull()
  })

  it('shows the flags of an answer in words', async () => {
    const flagged = scored('a1', '1', 0, {
      suggestion: {
        id: 'sg',
        mark: 0,
        mark_step: 0.5,
        flags: ['blank', 'low_ocr'],
        reasons: ['No text was read.'],
        relevance: null,
        created_at: '2026-10-06T08:05:00Z',
        criteria: [],
      },
    })
    await open(start({ answers: [flagged, scored('a2', '2', 1)] }))
    const flags = screen.getByRole('list', { name: 'Flags' })
    expect(flags).toHaveTextContent('Blank answer.')
    expect(flags).toHaveTextContent('Low OCR confidence.')
    expect(screen.getByRole('list', { name: 'Notes' })).toHaveTextContent('No text was read.')
  })

  it('approves the AI mark as it is, with the answer version, and moves to the next answer', async () => {
    const { mock, user } = await open(start())
    await user.click(screen.getByRole('button', { name: 'Approve answer' }))
    await waitFor(() => expect(mock.callsTo(`POST ${B}/answers/a1/approve`)).toHaveLength(1))
    expect(lastBody(mock, `POST ${B}/answers/a1/approve`)).toEqual({
      expected_version: 2,
      teacher_mark: null,
      tags: [],
      remarks: '',
    })
    expect(await screen.findByText('Question 1 approved.')).toBeVisible()
    // now on question 2
    expect(await screen.findByText('Define resonance.')).toBeVisible()
    expect(screen.getByRole('progressbar', { name: 'Approval progress' })).toHaveAttribute(
      'aria-valuenow',
      '1',
    )
    expect(screen.getByText('1 of 2 answers approved')).toBeVisible()
  })

  it('overrides the mark in the paper’s step only, and sends tags and remarks', async () => {
    const { mock, user } = await open(start())
    const marks = screen.getByLabelText(/Teacher marks/)
    expect(marks).toBeDisabled()
    expect(marks).toHaveValue(2)
    expect(marks).toHaveAttribute('step', '0.5')
    expect(marks).toHaveAttribute('max', '3')

    await user.click(screen.getByRole('switch', { name: 'Override AI Score' }))
    expect(marks).toBeEnabled()
    await user.clear(marks)
    await user.type(marks, '2.3')
    expect(await screen.findByRole('alert')).toHaveTextContent('steps of 0.5')
    expect(screen.getByRole('button', { name: 'Approve answer' })).toBeDisabled()
    await user.clear(marks)
    await user.type(marks, '3.5')
    expect(await screen.findByRole('alert')).toHaveTextContent('between 0 and 3')
    await user.clear(marks)
    await user.type(marks, '2.5')
    expect(screen.queryByRole('alert')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Partially correct' }))
    await user.click(screen.getByRole('button', { name: 'Key point missing' }))
    await user.type(screen.getByLabelText('Remarks for the student'), 'Mention why.')
    await user.click(screen.getByRole('button', { name: 'Approve answer' }))
    await waitFor(() => expect(mock.callsTo(`POST ${B}/answers/a1/approve`)).toHaveLength(1))
    expect(lastBody(mock, `POST ${B}/answers/a1/approve`)).toEqual({
      expected_version: 2,
      teacher_mark: 2.5,
      tags: ['Partially correct', 'Key point missing'],
      remarks: 'Mention why.',
    })
  })

  it('lets the teacher add and remove their own feedback tags, and remembers them', async () => {
    const { user } = await open(start())
    await user.type(screen.getByLabelText('New feedback tag'), 'Cite the law')
    await user.click(screen.getByRole('button', { name: 'Add tag' }))
    expect(screen.getByRole('button', { name: 'Cite the law' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(JSON.parse(localStorage.getItem('tarn.feedback-tags') ?? '[]')).toEqual(['Cite the law'])

    await user.click(
      screen.getByRole('button', { name: 'Remove the tag Cite the law from my list' }),
    )
    expect(screen.queryByRole('button', { name: 'Cite the law' })).toBeNull()
    expect(JSON.parse(localStorage.getItem('tarn.feedback-tags') ?? '[]')).toEqual([])
  })

  it('needs the teacher’s own mark when the AI gave none', async () => {
    const manual = scored('a1', '1', 0, {
      suggestion: {
        id: 'sg',
        mark: null,
        mark_step: 0.5,
        flags: ['mark_manually'],
        reasons: [],
        relevance: null,
        created_at: '2026-10-06T08:05:00Z',
        criteria: [],
      },
    })
    const { mock, user } = await open(start({ answers: [manual, scored('a2', '2', 1)] }))
    expect(screen.getByText('No AI mark')).toBeVisible()
    expect(screen.getByRole('switch', { name: 'Override AI Score' })).toBeDisabled()
    expect(screen.getByRole('switch', { name: 'Override AI Score' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Approve answer' })).toBeDisabled()
    await user.type(screen.getByLabelText(/Teacher marks/), '1')
    await user.click(screen.getByRole('button', { name: 'Approve answer' }))
    await waitFor(() => expect(mock.callsTo(`POST ${B}/answers/a1/approve`)).toHaveLength(1))
    expect(lastBody(mock, `POST ${B}/answers/a1/approve`)).toMatchObject({ teacher_mark: 1 })
  })

  it('waits for a new suggestion before an answer can be approved', async () => {
    await open(
      start({ answers: [scored('a1', '1', 2, { rescore_pending: true }), scored('a2', '2', 1)] }),
    )
    expect(screen.getByText('Suggested AI Marks: updating…')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Approve answer' })).toBeDisabled()
  })

  it('skips an answer, comes back to it, and shows it in the strip', async () => {
    const { mock, user } = await open(start())
    await user.click(screen.getByRole('button', { name: 'Skip for now' }))
    await waitFor(() => expect(mock.callsTo(`POST ${B}/answers/a1/skip`)).toHaveLength(1))
    expect(lastBody(mock, `POST ${B}/answers/a1/skip`)).toEqual({ expected_version: 2 })
    expect(await screen.findByText('Define resonance.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Question 1, skipped' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Approve answer' }))
    // question 2 is approved; the skipped question 1 is the one left, so it comes back
    expect(await screen.findByText('Question 2 approved.')).toBeVisible()
    expect(await screen.findByText('State Lenz’s law.')).toBeVisible()
    expect(screen.getByText('Skipped')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Question 2, approved' })).toBeVisible()
  })

  it('moves between answers with Previous, Next and the strip', async () => {
    const { user } = await open(start())
    const prev = screen.getByRole('button', { name: 'Previous answer' })
    expect(prev).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next answer' }))
    expect(await screen.findByText('Define resonance.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Question 2, to approve' })).toHaveAttribute(
      'aria-current',
      'step',
    )
    expect(screen.getByRole('button', { name: 'Next answer' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Previous answer' }))
    expect(await screen.findByText('State Lenz’s law.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Question 2, to approve' }))
    expect(await screen.findByText('Define resonance.')).toBeVisible()
    // unattempted question 3 has no chip
    expect(screen.queryByRole('button', { name: /Question 3/ })).toBeNull()
  })

  it('keeps the place in the address', async () => {
    await open(start(), {}, '&answer=2')
    expect(await screen.findByText('Define resonance.')).toBeVisible()
  })

  it('says when a question of the paper is not linked to the bank', async () => {
    const answers = [scored('a1', '1', 2), scored('a2', '3', 1)]
    await open(start({ answers }), {}, '&answer=3')
    expect(await screen.findByText(/No question of the bank is linked/)).toBeVisible()
  })

  describe('lock', () => {
    it('is read-only, with the holder named, while another teacher has the booklet open', async () => {
      const held = start({
        lock: {
          holder_id: 'u-9',
          holder_name: 'Dr. Other',
          acquired_at: '2026-10-06T08:10:00Z',
          expires_at: '2026-10-06T08:25:00Z',
          mine: false,
        },
      })
      const { mock } = await open(held, {
        [`POST ${B}/lock`]: {
          status: 423,
          json: { detail: 'Held.', holder_id: 'u-9', expires_at: '2026-10-06T08:25:00Z' },
        },
      })
      const notes = await screen.findAllByText(/Dr\. Other has this booklet open/)
      expect(notes.length).toBeGreaterThan(0)
      expect(screen.getByRole('button', { name: 'Approve answer' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Skip for now' })).toBeDisabled()
      expect(screen.getByRole('switch', { name: 'Override AI Score' })).toBeDisabled()
      expect(screen.queryByText(/You have this booklet open\./)).toBeNull()
      expect(mock.callsTo(`POST ${B}/answers/a1/approve`)).toHaveLength(0)
    })

    it('reopens the booklet when a decision finds the lock gone (423)', async () => {
      const { mock, user } = await open(start(), {
        [`POST ${B}/answers/a1/approve`]: { status: 423, json: { detail: 'Not open.' } },
      })
      await user.click(screen.getByRole('button', { name: 'Approve answer' }))
      expect(await screen.findByText(/You no longer have this booklet open/)).toBeVisible()
      await waitFor(() => expect(mock.callsTo(`POST ${B}/lock`).length).toBeGreaterThanOrEqual(2))
    })

    it('reloads and says so when the answer changed meanwhile (409)', async () => {
      const { user } = await open(start(), {
        [`POST ${B}/answers/a1/approve`]: { status: 409, json: { detail: 'The answer changed.' } },
      })
      await user.click(screen.getByRole('button', { name: 'Approve answer' }))
      expect(await screen.findByText(/The screen was reloaded/)).toBeVisible()
    })
  })

  describe('diagram check', () => {
    const doc = {
      schema_version: '1.0',
      student_diagram_id: 'd-1',
      similarity: 0.54,
      sub_scores: { nodes: 0.68, edges: 0.38, labels: 0.67 },
      nodes: [
        { id: 'n1', shape: 'terminal', label: 'Start', matched_ref: 'r1', shape_match: true },
        { id: 'n2', shape: 'process', label: 'Add', matched_ref: null, shape_match: null },
      ],
      reference_nodes: [
        { id: 'r1', shape: 'terminal', label: 'Start', matched_student: 'n1' },
        { id: 'r2', shape: 'terminal', label: 'Stop', matched_student: null },
      ],
      edges: [
        { ref: ['r1', 'r2'], student: null, status: 'missing', student_edge: null },
        { ref: null, student: ['n1', 'n2'], status: 'extra', student_edge: 'e1' },
        { ref: ['r2', 'r1'], student: ['n1', 'n2'], status: 'reversed', student_edge: 'e1' },
      ],
    }

    it('marks missing, extra and reversed elements', async () => {
      await open(start(), {
        [`GET ${B}/diagrams`]: {
          json: [
            {
              id: 'd-1',
              segment_id: 's1',
              region_id: 'r1',
              version: 1,
              kind: 'flowchart',
              box: [90, 90, 400, 400],
              graph: graph(),
            },
          ],
        },
        [`GET ${B}/answers/a1/diagram-comparisons`]: {
          json: [
            {
              criterion_id: 'c-3',
              criterion_version: 1,
              credit: 0.5,
              similarity: 0.54,
              flags: [],
              document: doc,
            },
          ],
        },
      })
      const check = await screen.findByRole('region', { name: 'Diagram check' })
      expect(check).toHaveTextContent('Similarity 54%')
      expect(within(check).getByText('Missing').nextSibling).toHaveTextContent('box “Stop”')
      expect(within(check).getByText('Missing').nextSibling).toHaveTextContent('arrow Start → Stop')
      expect(within(check).getByText('Extra').nextSibling).toHaveTextContent('box “Add”')
      expect(within(check).getByText('Reversed').nextSibling).toHaveTextContent(
        'drawn Start → Add, expected Stop → Start',
      )
      await waitFor(() => expect(check.querySelector('[data-mark="node-extra"]')).not.toBeNull())
      expect(check.querySelector('[data-mark="node-matched"]')).not.toBeNull()
    })
  })

  describe('booklet summary', () => {
    async function summary(initial = start(), extra: Routes = {}) {
      const s = await open(initial, extra)
      await s.user.click(screen.getByRole('button', { name: 'Save & Export Grade Sheet' }))
      const table = await screen.findByRole('table')
      return { ...s, table }
    }

    it('lists every question with its mark and which marks do not count', async () => {
      const { table } = await summary(
        start({
          totals: { total: 2, max_marks: 6, slots: slots(false) },
        }),
      )
      const rows = within(table).getAllByRole('row')
      expect(rows).toHaveLength(4)
      expect(rows[1]).toHaveTextContent('Q1')
      expect(rows[1]).toHaveTextContent('2 (AI) / 3')
      expect(rows[2]).toHaveTextContent('Not counted: best N')
      expect(rows[3]).toHaveTextContent('Not attempted')
      expect(screen.getByText('2 / 6')).toBeVisible()
      expect(screen.getByText(/provisional until approved/)).toBeVisible()
    })

    it('explains an OR alternative that does not count', async () => {
      const { table } = await summary(
        start({
          totals: {
            total: 2,
            max_marks: 6,
            slots: [
              { section_label: 'A', slot_label: '1', mark: 2, counted: true, outcome: 'counted' },
              {
                section_label: 'A',
                slot_label: '2',
                mark: 1,
                counted: false,
                outcome: 'not counted: other OR alternative scored higher',
              },
            ],
          },
        }),
      )
      expect(table).toHaveTextContent('Not counted: the other OR alternative scored higher')
    })

    it('keeps Approve booklet disabled until every answer is approved', async () => {
      const { user } = await summary()
      const approve = screen.getByRole('button', { name: 'Approve booklet' })
      expect(approve).toBeDisabled()
      expect(screen.getByText('2 of 2 answers still need your approval.')).toBeVisible()
      await user.click(screen.getByRole('button', { name: 'Open question 1' }))
      expect(await screen.findByText('State Lenz’s law.')).toBeVisible()
    })

    it('approves the booklet with the booklet version once every answer is approved', async () => {
      const { mock, user } = await open(start())
      await user.click(screen.getByRole('button', { name: 'Approve answer' }))
      await screen.findByText('Define resonance.')
      await user.click(screen.getByRole('button', { name: 'Approve answer' }))
      // the last approval leads to the summary
      expect(await screen.findByText(/That was the last one/)).toBeVisible()
      const approve = await screen.findByRole('button', { name: 'Approve booklet' })
      await waitFor(() => expect(approve).toBeEnabled())
      await user.click(approve)
      await waitFor(() => expect(mock.callsTo(`POST ${B}/approve`)).toHaveLength(1))
      expect(
        (mock.callsTo(`POST ${B}/approve`)[0]?.body as { expected_version: number })
          .expected_version,
      ).toBeGreaterThanOrEqual(5)
      expect(await screen.findByText(/Booklet approved\. Result sheet v1 is stored/)).toBeVisible()
      const sheets = await screen.findByRole('region', { name: 'Result sheets' })
      expect(sheets).toHaveTextContent('Result sheet v1')
      expect(within(sheets).getByRole('button', { name: 'Download PDF v1' })).toBeVisible()
      expect(screen.queryByRole('button', { name: 'Approve booklet' })).toBeNull()
    })

    it('reopens an approved answer as an amendment, and issues v2 when it is approved again', async () => {
      const approvedAnswer = (id: string, label: string, mark: number): Answer => ({
        ...scored(id, label, mark),
        status: 'approved',
        approval: {
          id: `rv-${id}`,
          ai_mark: mark,
          teacher_mark: mark,
          overridden: false,
          tags: ['Correct'],
          remarks: 'Good.',
          reviewer_id: 'u-1',
          reviewed_at: '2026-10-06T08:30:00Z',
        },
      })
      const approved = start({
        status: 'approved',
        approved: true,
        answers: [approvedAnswer('a1', '1', 2), approvedAnswer('a2', '2', 1)],
        sheets: [sheet(1, 3)],
      })
      const { mock, user } = await summary(approved)
      expect(screen.getByText(/Booklet approved: result sheet v1 is the current one/)).toBeVisible()

      await user.click(screen.getByRole('button', { name: 'Reopen to amend question 1' }))
      const group = screen.getByRole('group', { name: 'Reopen an answer' })
      expect(group).toHaveTextContent('issued result sheet stays valid')
      await user.type(within(group).getByLabelText('Reason (optional)'), 'Marked too low.')
      await user.click(within(group).getByRole('button', { name: 'Open amendment' }))
      await waitFor(() => expect(mock.callsTo(`POST ${B}/answers/a1/reopen`)).toHaveLength(1))
      expect(lastBody(mock, `POST ${B}/answers/a1/reopen`)).toEqual({
        expected_version: 2,
        reason: 'Marked too low.',
      })

      // the answer opens as a draft, with the badge and the reason
      expect(await screen.findByText(/Reason: Marked too low\./)).toBeVisible()
      expect(screen.getByText('Amendment draft')).toBeVisible()
      expect(screen.getByText('Draft amendment')).toBeVisible()
      await user.click(screen.getByRole('switch', { name: 'Override AI Score' }))
      const marks = screen.getByLabelText(/Teacher marks/)
      await user.clear(marks)
      await user.type(marks, '3')
      await user.click(screen.getByRole('button', { name: 'Approve amendment' }))
      await waitFor(() => expect(mock.callsTo(`POST ${B}/answers/a1/approve`)).toHaveLength(1))
      expect(lastBody(mock, `POST ${B}/answers/a1/approve`)).toMatchObject({
        teacher_mark: 3,
        tags: ['Correct'],
        remarks: 'Good.',
      })

      // the amendment is complete: the summary lists v2 and v1
      expect(await screen.findByText(/The amendment is complete/)).toBeVisible()
      const sheets = await screen.findByRole('region', { name: 'Result sheets' })
      await waitFor(() => expect(sheets).toHaveTextContent('Result sheet v2'))
      expect(sheets).toHaveTextContent('Result sheet v1')
      expect(sheets).toHaveTextContent('Amended: question 1')
      expect(within(sheets).getByText('Current').closest('li')).toHaveTextContent('v2')
      expect(within(sheets).getByRole('button', { name: 'Download PDF v1' })).toBeVisible()
      expect(within(sheets).getByRole('button', { name: 'Download PDF v2' })).toBeVisible()
    })

    it('withdraws an amendment draft from the summary', async () => {
      const draft = (): Answer => ({
        ...scored('a1', '1', 2),
        status: 'suggested',
        draft: {
          amendment_id: 'am-1',
          reason: '',
          opened_by: 'u-1',
          opened_at: '2026-10-06T09:30:00Z',
        },
        approval: {
          id: 'rv-a1',
          ai_mark: 2,
          teacher_mark: 2,
          overridden: false,
          tags: [],
          remarks: '',
          reviewer_id: 'u-1',
          reviewed_at: '2026-10-06T08:30:00Z',
        },
      })
      const { mock, user } = await summary(
        start({
          status: 'amendment_in_progress',
          approved: true,
          amendment_in_progress: true,
          answers: [draft(), { ...scored('a2', '2', 1), status: 'approved' }],
          sheets: [sheet(1, 3)],
        }),
      )
      expect(
        screen.getByText(/Result sheet v2 is issued when the last one is approved/),
      ).toBeVisible()
      await user.click(screen.getByRole('button', { name: 'Withdraw amendment of question 1' }))
      await waitFor(() => expect(mock.callsTo(`POST ${B}/answers/a1/withdraw`)).toHaveLength(1))
      expect(await screen.findByText(/Amendment of question 1 withdrawn/)).toBeVisible()
    })

    it('goes back to the answers', async () => {
      const { user } = await summary()
      await user.click(screen.getByRole('button', { name: 'Back to the answers' }))
      expect(await screen.findByRole('region', { name: 'Panel A: Answer Mapping' })).toBeVisible()
    })
  })

  it('goes back to the segments', async () => {
    const { user } = await open(start())
    await user.click(screen.getByRole('button', { name: 'Back to Segments' }))
    expect(
      await screen.findByRole('heading', { name: 'AI Cropped & OCR Extracted Segments' }),
    ).toBeVisible()
  })

  it('asks for a booklet when step 3 is opened without one', async () => {
    mockApi(base())
    renderApp('/evaluate?step=3')
    expect(await screen.findByText('Choose a booklet first')).toBeVisible()
  })

  it('says so when the booklet is still being read', async () => {
    mockApi(base({ [`GET ${B}`]: { json: detail({ status: 'reading' }) } }))
    renderApp(`/evaluate?booklet=${BOOKLET}&step=3`)
    expect(await screen.findByText('This booklet is not ready for evaluation')).toBeVisible()
  })

  it('says so for a booklet that does not exist', async () => {
    mockApi(base({ [`GET ${B}`]: { status: 404, json: { detail: 'Not found.' } } }))
    renderApp(`/evaluate?booklet=${BOOKLET}&step=3`)
    expect(await screen.findByText('This booklet could not be opened')).toBeVisible()
  })

  it('releases the lock on leaving the screen', async () => {
    const { mock, view } = await open(start())
    view.unmount()
    await waitFor(() => expect(mock.callsTo(`DELETE ${B}/lock`)).toHaveLength(1), { timeout: 2000 })
  })
})
