import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { components } from '../../api/schema'
import { renderApp } from '../../test/render'
import { noResults, smeSession } from '../../test/smeFixtures'
import { optionsOf } from './GlobalSearch'

afterEach(() => vi.unstubAllGlobals())

const FOUND: components['schemas']['SearchOut'] = {
  units: [
    {
      unit_id: 'unit-1',
      code: 'U-0001',
      paper_title: 'Physics mid-term',
      exam_code: 'PHY-MID',
      year: '2024',
      due_date: '2026-11-20',
      state: 'open',
    },
  ],
  students: [{ booklet_id: 'b-1', unit_id: 'unit-1', name: 'Mira Rao', usn: 'MR26A0001' }],
  papers: [
    {
      blueprint_id: 'paper-1',
      title: 'Physics mid-term',
      exam_code: 'PHY-MID',
      descriptor: 'Mid-term',
      subject_id: 'sub-1',
    },
  ],
  schemas: [],
  questions: [
    { question_id: 'q-1', code: 'PHY-Q1', text: 'State the mid-point rule.', subject_id: 'sub-1' },
  ],
}

function withResults() {
  return smeSession({ 'GET /api/v1/search': { json: FOUND } })
}

describe('the global search (P37, D228)', () => {
  it('waits for two characters, then shows the groups', async () => {
    const api = withResults()
    const user = userEvent.setup()
    renderApp('/sme')
    const box = await screen.findByRole('combobox', { name: /Search/ })
    await user.type(box, 'm')
    await new Promise((r) => setTimeout(r, 350))
    expect(api.callsTo('GET /api/v1/search')).toHaveLength(0)
    expect(box).toHaveAttribute('aria-expanded', 'false')
    await user.type(box, 'id')
    const list = await screen.findByRole('listbox', { name: 'Search results' })
    await within(list).findByRole('option', { name: /U-0001/ })
    expect(api.callsTo('GET /api/v1/search').at(-1)?.query.get('q')).toBe('mid')
    const groups = within(list).getAllByRole('group')
    expect(groups.map((g) => within(g).getAllByRole('option').length)).toEqual([1, 1, 1, 1])
    expect(within(list).getByRole('group', { name: 'Units' })).toBeVisible()
    expect(within(list).getByRole('group', { name: 'Students' })).toHaveTextContent('Mira Rao')
    expect(within(list).getByRole('group', { name: 'Question papers' })).toBeVisible()
    expect(within(list).getByRole('group', { name: 'Questions' })).toHaveTextContent('PHY-Q1')
    expect(within(list).queryByRole('group', { name: 'Schemas' })).toBeNull()
    expect(box).toHaveAttribute('aria-expanded', 'true')
  })

  it('moves with the arrows and opens the chosen screen with Enter', async () => {
    const api = withResults()
    const user = userEvent.setup()
    renderApp('/sme/profile')
    const box = await screen.findByRole('combobox', { name: /Search/ })
    await user.type(box, 'mira')
    const list = await screen.findByRole('listbox', { name: 'Search results' })
    await within(list).findByRole('option', { name: /Mira Rao/ })
    await user.keyboard('{ArrowDown}')
    const first = within(list).getByRole('option', { name: /U-0001/ })
    expect(first).toHaveAttribute('aria-selected', 'true')
    expect(box).toHaveAttribute('aria-activedescendant', first.id)
    await user.keyboard('{ArrowDown}')
    expect(within(list).getByRole('option', { name: /Mira Rao/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    // Up from the first wraps to the last.
    await user.keyboard('{ArrowUp}{ArrowUp}')
    expect(within(list).getByRole('option', { name: /PHY-Q1/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    // The student's question list reads the booklet.
    await waitFor(() => expect(api.callsTo('GET /api/v1/booklets/b-1').length).toBeGreaterThan(0))
    expect(box).toHaveValue('')
  })

  it('opens an option on click and closes on Escape', async () => {
    const api = withResults()
    const user = userEvent.setup()
    renderApp('/sme')
    const box = await screen.findByRole('combobox', { name: /Search/ })
    await user.type(box, 'phy')
    const list = await screen.findByRole('listbox', { name: 'Search results' })
    await within(list).findByRole('option', { name: /PHY-Q1/ })
    await user.keyboard('{Escape}')
    expect(box).toHaveAttribute('aria-expanded', 'false')
    expect(box).toHaveValue('phy')
    await user.keyboard('{ArrowDown}')
    await user.click(within(list).getByRole('option', { name: /U-0001/ }))
    await waitFor(() => expect(api.callsTo('GET /api/v1/units/unit-1').length).toBeGreaterThan(0))
  })

  it('says when nothing matches', async () => {
    smeSession({ 'GET /api/v1/search': { json: noResults } })
    const user = userEvent.setup()
    renderApp('/sme')
    await user.type(await screen.findByRole('combobox', { name: /Search/ }), 'zz')
    expect(await screen.findByText('No matches.')).toBeVisible()
  })

  it('says when the search fails (too many searches)', async () => {
    smeSession({ 'GET /api/v1/search': { status: 429, json: { detail: 'Too many' } } })
    const user = userEvent.setup()
    renderApp('/sme')
    await user.type(await screen.findByRole('combobox', { name: /Search/ }), 'zz')
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be run')
  })
})

describe('search options', () => {
  it('link each hit to its screen', () => {
    expect(optionsOf(FOUND).map((o) => o.to)).toEqual([
      '/sme/units/unit-1',
      '/sme/units/unit-1/booklets/b-1',
      '/sme/papers/paper-1',
      '/sme/questions/q-1',
    ])
    const schema = optionsOf({
      ...noResults,
      schemas: [
        { blueprint_id: 's-1', title: 'Matrix', exam_code: '', descriptor: '', subject_id: 'x' },
      ],
    })
    expect(schema.map((o) => o.to)).toEqual(['/sme/schemas/s-1'])
  })
})
