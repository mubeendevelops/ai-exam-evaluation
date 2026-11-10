import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { physics } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'
import { blueprintOf, paperCardOf, paperDocumentOf, verdictOf } from '../../test/schemaFixtures'
import { smeSession } from '../../test/smeFixtures'

vi.mock('../../lib/paperPdf', () => ({
  openPdf: vi.fn(async () => ({
    pages: 1,
    draw: () => ({ done: Promise.resolve(), cancel: () => undefined }),
    close: () => undefined,
  })),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const midterm = paperCardOf()
const board = paperCardOf({
  id: 'paper-2',
  title: 'Organic Reactions',
  descriptor: 'Board Final',
  description: '',
  exam_code: '',
  duration_minutes: null,
  total_marks: 70,
  in_use: true,
})

function listing(items = [midterm, board], total = items.length) {
  return { items, total, limit: 12, offset: 0, descriptors: ['Board Final', 'Mid term 2026 batch'] }
}

function session(extra: Parameters<typeof smeSession>[0] = {}) {
  return smeSession({
    'GET /api/v1/papers': { json: listing() },
    'GET /api/v1/blueprints': { json: [] },
    'POST /api/v1/blueprints/validate': verdictOf,
    ...extra,
  })
}

async function cards() {
  return within(await screen.findByRole('list', { name: 'Question papers' }))
}

describe('Manage Question Papers (P34)', () => {
  it('shows a card per paper as the mock does, with its actions', async () => {
    session()
    renderApp('/sme/papers')
    const list = await cards()
    const card = list.getByRole('listitem', { name: 'Physics Mid Term 2026' })
    expect(card).toHaveTextContent('3 Hours • 100 Marks')
    expect(card).toHaveTextContent('Mechanics and optics.')
    expect(card).toHaveTextContent('Descriptor: Mid term 2026 batch')
    expect(card).toHaveTextContent('Exam code PHY-MID-26')
    expect(card).toHaveTextContent('Updated Oct 2026')
    expect(within(card).getByText('Physics')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'Edit Physics Mid Term 2026' })).toHaveAttribute(
      'href',
      '/sme/papers/paper-1',
    )
    for (const name of ['Clone', 'Delete', 'Preview', 'Download the PDF of']) {
      expect(
        within(card).getByRole('button', { name: `${name} Physics Mid Term 2026` }),
      ).toBeInTheDocument()
    }
    const other = list.getByRole('listitem', { name: 'Organic Reactions' })
    expect(other).toHaveTextContent('70 Marks')
    expect(other).toHaveTextContent('No exam code yet · In use by a unit')
    expect(other).toHaveTextContent('No description yet.')
    expect(screen.getByText(/Showing/)).toHaveTextContent('Showing 1 - 2 of 2 Question Papers')
    expect(within(screen.getByRole('banner')).getByText('Manage Question Papers')).toBeVisible()
  })

  it('searches by name or descriptor, filters by date and tag, all in the address', async () => {
    const mock = session()
    const user = userEvent.setup()
    renderApp('/sme/papers')
    await cards()
    const tag = screen.getByRole('combobox', { name: 'Filter Tag' })
    expect(
      within(tag)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['All Descriptors', 'Board Final', 'Mid term 2026 batch'])
    await user.type(
      screen.getByRole('searchbox', { name: 'Search by paper name or descriptor' }),
      'mid term',
    )
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/papers').some((c) => c.query.get('q') === 'mid term')).toBe(
        true,
      ),
    )
    await user.selectOptions(tag, 'Board Final')
    await user.type(screen.getByLabelText('Created on'), '2026-10-15')
    await waitFor(() => {
      const last = mock.callsTo('GET /api/v1/papers').at(-1)
      expect(last?.query.get('q')).toBe('mid term')
      expect(last?.query.get('descriptor')).toBe('Board Final')
      expect(last?.query.get('created_on')).toBe('2026-10-15')
      expect(last?.query.get('offset')).toBe('0')
    })
  })

  it('pages twelve at a time', async () => {
    const twelve = Array.from({ length: 12 }, (_, i) =>
      paperCardOf({ id: `p-${i}`, title: `Paper ${i + 1}` }),
    )
    const mock = session({
      'GET /api/v1/papers': (call) =>
        call.query.get('offset') === '12'
          ? { json: { ...listing([board], 13), offset: 12 } }
          : { json: listing(twelve, 13) },
    })
    const user = userEvent.setup()
    renderApp('/sme/papers')
    await cards()
    expect(screen.getByText(/Showing/)).toHaveTextContent('Showing 1 - 12 of 13 Question Papers')
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByRole('listitem', { name: 'Organic Reactions' })).toBeInTheDocument()
    expect(screen.getByText(/Showing/)).toHaveTextContent('Showing 13 - 13 of 13 Question Papers')
    expect(mock.callsTo('GET /api/v1/papers').at(-1)?.query.get('limit')).toBe('12')
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
  })

  it('creates a paper from a schema with a suggested descriptor and opens it', async () => {
    const made = blueprintOf('paper-9', paperDocumentOf({ title: 'Physics Unit Test' }))
    const mock = session({
      'GET /api/v1/blueprints': {
        json: [blueprintOf('schema-1', { ...paperDocumentOf(), kind: 'schema', title: 'Matrix' })],
      },
      'POST /api/v1/papers': { status: 201, json: made },
      'GET /api/v1/blueprints/paper-9': { json: made },
    })
    const user = userEvent.setup()
    renderApp('/sme/papers')
    await cards()
    await user.click(screen.getByRole('button', { name: /Create Question Paper/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Create / Clone Question Paper' })
    const save = within(dialog).getByRole('button', { name: 'Save & Generate Paper' })
    expect(save).toBeDisabled()
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Question Paper Name' }),
      'Physics Unit Test',
    )
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Subject' }), physics.id)
    const descriptor = within(dialog).getByRole('combobox', { name: 'Popular Descriptor' })
    expect(descriptor).toHaveAttribute('list')
    await user.type(descriptor, 'Board Final')
    await user.selectOptions(
      await within(dialog).findByRole('combobox', { name: 'Start from schema (optional)' }),
      'schema-1',
    )
    await user.click(save)
    await waitFor(() => expect(mock.callsTo('POST /api/v1/papers')).toHaveLength(1))
    expect(mock.callsTo('POST /api/v1/papers')[0]?.body).toEqual({
      title: 'Physics Unit Test',
      subject_id: physics.id,
      descriptor: 'Board Final',
      schema_id: 'schema-1',
    })
    expect(await screen.findByRole('heading', { name: 'Exam Blueprint' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue(
      'Physics Unit Test',
    )
  })

  it('clones a paper as "(Copy)" keeping its subject, then opens the copy', async () => {
    const copy = blueprintOf('paper-3', paperDocumentOf({ title: 'Physics Mid Term 2026 (Copy)' }))
    const mock = session({
      'POST /api/v1/blueprints/paper-1/clone': { status: 201, json: copy },
      'GET /api/v1/blueprints/paper-3': { json: copy },
    })
    const user = userEvent.setup()
    renderApp('/sme/papers')
    const list = await cards()
    await user.click(list.getByRole('button', { name: 'Clone Physics Mid Term 2026' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create / Clone Question Paper' })
    expect(within(dialog).getByRole('textbox', { name: 'Question Paper Name' })).toHaveValue(
      'Physics Mid Term 2026 (Copy)',
    )
    expect(within(dialog).queryByRole('combobox', { name: 'Subject' })).toBeNull()
    expect(dialog).toHaveTextContent('Subject: Physics')
    await user.click(within(dialog).getByRole('button', { name: 'Save copy' }))
    await waitFor(() =>
      expect(mock.callsTo('POST /api/v1/blueprints/paper-1/clone')[0]?.body).toEqual({
        title: 'Physics Mid Term 2026 (Copy)',
        descriptor: 'Mid term 2026 batch',
      }),
    )
    expect(await screen.findByRole('heading', { name: 'Exam Blueprint' })).toBeInTheDocument()
  })

  it('deletes a paper after the server allows it, and says why it cannot', async () => {
    const mock = session({
      'GET /api/v1/blueprints/paper-1/deletion': { json: { allowed: true, blockers: [] } },
      'DELETE /api/v1/blueprints/paper-1': { status: 204 },
      'GET /api/v1/blueprints/paper-2/deletion': {
        json: { allowed: false, blockers: ['Unit U-0001 (open) uses it.'] },
      },
    })
    const user = userEvent.setup()
    renderApp('/sme/papers')
    const list = await cards()
    await user.click(list.getByRole('button', { name: 'Delete Organic Reactions' }))
    let dialog = await screen.findByRole('dialog', { name: 'Delete this paper?' })
    expect(await within(dialog).findByText('Unit U-0001 (open) uses it.')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await user.click(list.getByRole('button', { name: 'Delete Physics Mid Term 2026' }))
    dialog = await screen.findByRole('dialog', { name: 'Delete this paper?' })
    await user.click(await within(dialog).findByRole('button', { name: 'Delete permanently' }))
    await waitFor(() => expect(mock.callsTo('DELETE /api/v1/blueprints/paper-1')).toHaveLength(1))
  })

  it('offers a first paper when there is none, and clearing a filter that matches none', async () => {
    session({ 'GET /api/v1/papers': { json: listing([], 0) } })
    renderApp('/sme/papers')
    expect(await screen.findByRole('heading', { name: 'No question papers yet' })).toBeVisible()
    cleanup()
    vi.unstubAllGlobals()
    session({ 'GET /api/v1/papers': { json: listing([], 0) } })
    const user = userEvent.setup()
    renderApp('/sme/papers?descriptor=Board%20Final')
    expect(await screen.findByRole('heading', { name: 'No paper matches' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clear the filters' }))
    expect(await screen.findByRole('heading', { name: 'No question papers yet' })).toBeVisible()
  })
})
