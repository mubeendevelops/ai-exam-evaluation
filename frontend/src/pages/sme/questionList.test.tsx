import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BOOKLET, UNIT, answer, detail, review } from '../../test/evaluateFixtures'
import { renderApp } from '../../test/render'
import { unitDetailOf, unitOf } from '../../test/adminFixtures'
import { smeSession } from '../../test/smeFixtures'

afterEach(() => vi.unstubAllGlobals())

const B = `/api/v1/booklets/${BOOKLET}`
const FROM = '?q=one&status=in_review'
const PATH = `/sme/units/${UNIT}/booklets/${BOOKLET}?from=${encodeURIComponent(FROM)}`

const ANSWERS = [
  answer('a1', '1', 2, {
    status: 'approved',
    approval: {
      id: 'r-1',
      ai_mark: 2,
      teacher_mark: 2.5,
      overridden: true,
      tags: [],
      remarks: '',
      reviewer_id: 'u-1',
      reviewed_at: '2026-10-06T08:20:00Z',
    },
  }),
  answer('a2', '2', 1.5, {
    suggestion: {
      ...answer('x', '2', 1.5).suggestion!,
      flags: ['low_ocr', 'off_target'],
    },
  }),
  answer('a3', '3', null, {
    status: 'skipped',
    suggestion: { ...answer('x', '3', null).suggestion!, flags: ['mark_manually'] },
  }),
  answer('a4', '4', null, { attempted: false, suggestion: null }),
]

function session() {
  return smeSession({
    [`GET ${B}`]: { json: detail({ status: 'in_review' }) },
    [`GET ${B}/review`]: { json: review({ answers: ANSWERS }) },
  })
}

describe('Question list (P32, D217)', () => {
  it('lists each answer with its question, state, mark and flags', async () => {
    const mock = session()
    renderApp(PATH)
    expect(await screen.findByRole('heading', { name: 'Test Student One' })).toBeVisible()
    const list = await screen.findByRole('list', { name: 'Questions' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(4)
    expect(items[0]).toHaveTextContent('Q1')
    expect(items[0]).toHaveTextContent('Q-1') // the question's code
    expect(items[0]).toHaveTextContent('Approved: 2.5 / 3')
    expect(items[1]).toHaveTextContent('AI suggested: 1.5 / 3')
    expect(within(items[1]!).getByText('Low OCR confidence')).toBeInTheDocument()
    expect(within(items[1]!).getByText('Off target')).toBeInTheDocument()
    expect(items[1]).toHaveTextContent('Suggested')
    expect(items[2]).toHaveTextContent('Mark manually (out of 3)')
    expect(items[2]).toHaveTextContent('Skipped')
    expect(items[3]).toHaveTextContent('Not attempted')
    expect(within(items[3]!).queryByRole('link')).toBeNull()
    // Reading the list takes no lock.
    expect(mock.callsTo(`POST ${B}/lock`)).toHaveLength(0)
  })

  it('opens an answer in step 3, the pages in step 2, and goes back with the filter', async () => {
    session()
    renderApp(PATH)
    const keep = `&from=${encodeURIComponent(FROM)}`
    expect(await screen.findByRole('link', { name: 'Question 2: Suggested' })).toHaveAttribute(
      'href',
      `/evaluate?booklet=${BOOKLET}&step=3&answer=2${keep}`,
    )
    expect(screen.getByRole('link', { name: /Pages and segments/ })).toHaveAttribute(
      'href',
      `/evaluate?booklet=${BOOKLET}${keep}`,
    )
    expect(screen.getByRole('link', { name: /Back to Student Directory/ })).toHaveAttribute(
      'href',
      `/sme/units/${UNIT}${FROM}`,
    )
    expect(screen.queryByRole('link', { name: /View Final/ })).toBeNull() // not approved yet
  })

  it('offers View Final for an approved booklet: the summary with every sheet', async () => {
    smeSession({
      [`GET ${B}`]: { json: detail({ status: 'approved_amended' }) },
      [`GET ${B}/review`]: { json: review({ answers: ANSWERS, status: 'approved_amended' }) },
    })
    renderApp(`/sme/units/${UNIT}/booklets/${BOOKLET}`)
    expect(await screen.findByRole('link', { name: /View Final/ })).toHaveAttribute(
      'href',
      `/evaluate?booklet=${BOOKLET}&step=3&view=summary`,
    )
  })

  it('goes back to the student list with its filter in place', async () => {
    smeSession({
      [`GET ${B}`]: { json: detail({ status: 'in_review' }) },
      [`GET ${B}/review`]: { json: review({ answers: ANSWERS }) },
      [`GET /api/v1/units/${UNIT}`]: { json: unitDetailOf([], { ...unitOf(), matching: 0 }) },
    })
    const user = userEvent.setup()
    renderApp(PATH)
    await user.click(await screen.findByRole('link', { name: /Back to Student Directory/ }))
    expect(await screen.findByLabelText('Search student name or roll no.')).toHaveValue('one')
    expect(screen.getByLabelText('Status')).toHaveValue('in_review')
  })

  it('deletes the booklet after asking, then returns to the student list', async () => {
    const mock = smeSession({
      [`GET ${B}`]: { json: detail({ status: 'in_review' }) },
      [`GET ${B}/review`]: { json: review({ answers: ANSWERS }) },
      [`DELETE ${B}`]: { status: 204 },
      [`GET /api/v1/units/${UNIT}`]: { json: unitDetailOf([], { ...unitOf(), matching: 0 }) },
    })
    const user = userEvent.setup()
    renderApp(PATH)
    await user.click(await screen.findByRole('button', { name: 'Delete booklet' }))
    expect(mock.callsTo(`DELETE ${B}`)).toHaveLength(0) // asks first
    await user.click(screen.getByRole('button', { name: 'Yes, delete' }))
    expect(await screen.findByText(/The booklet was deleted/)).toBeVisible()
    expect(mock.callsTo(`DELETE ${B}`)).toHaveLength(1)
    expect(await screen.findByLabelText('Search student name or roll no.')).toHaveValue('one')
  })

  it('offers no delete once the unit is submitted', async () => {
    smeSession({
      [`GET ${B}`]: { json: detail({ status: 'approved', unit_state: 'submitted' }) },
      [`GET ${B}/review`]: { json: review({ answers: ANSWERS, status: 'approved' }) },
    })
    renderApp(PATH)
    await screen.findByRole('heading', { name: 'Test Student One' })
    expect(screen.queryByRole('button', { name: 'Delete booklet' })).toBeNull()
  })

  it('says so while the booklet is still being read', async () => {
    smeSession({ [`GET ${B}`]: { json: detail({ status: 'reading' }) } })
    renderApp(PATH)
    expect(await screen.findByText(/still reading this booklet/)).toBeVisible()
  })
})
