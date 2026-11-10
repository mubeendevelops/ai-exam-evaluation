import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlueprintDocument } from '../../lib/blueprint'
import { physics } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'
import { blueprintOf, cardOf, documentOf, verdictOf } from '../../test/schemaFixtures'
import { smeSession } from '../../test/smeFixtures'

beforeEach(() => globalThis.sessionStorage.clear())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const matrix = cardOf()
const rubric = cardOf({
  id: 'schema-2',
  title: 'Board Rubric',
  target_batch: '2024 batch',
  weightage_note: '',
  description: '',
  used_in: 1,
  updated_at: null,
})
const listing = { items: [rubric, matrix], batches: ['2024 batch', 'Class 12'] }

function session(extra: Parameters<typeof smeSession>[0] = {}) {
  return smeSession({
    'GET /api/v1/schemas': { json: listing },
    'POST /api/v1/blueprints/validate': verdictOf,
    ...extra,
  })
}

async function cards() {
  return within(await screen.findByRole('list', { name: 'Schemas' }))
}

describe('Manage Schema', () => {
  it('shows a card per schema with its batch, weightage, uses and update month', async () => {
    session()
    renderApp('/sme/schemas')
    const list = await cards()
    const card = list.getByRole('listitem', { name: 'Midterm Matrix' })
    expect(card).toHaveTextContent('Batch: Class 12')
    expect(card).toHaveTextContent('40% conceptual, 60% analytical')
    expect(card).toHaveTextContent('Used in 3 Question Papers')
    expect(card).toHaveTextContent('Updated Oct 2026')
    expect(within(card).getByText('Physics')).toBeInTheDocument()
    const other = list.getByRole('listitem', { name: 'Board Rubric' })
    expect(other).toHaveTextContent('Used in 1 Question Paper')
    expect(other).toHaveTextContent('No weightage description yet.')
    expect(within(card).getByRole('link', { name: 'Edit Midterm Matrix' })).toHaveAttribute(
      'href',
      '/sme/schemas/schema-1',
    )
    expect(screen.getByText('Showing 2 schemas')).toBeInTheDocument()
    // The header title and the sidebar point here.
    const banner = screen.getByRole('banner')
    expect(within(banner).getByText('Manage Schema')).toBeInTheDocument()
  })

  it('filters by name as you type and by the batches in use', async () => {
    const mock = session()
    const user = userEvent.setup()
    renderApp('/sme/schemas')
    await cards()
    const batch = screen.getByRole('combobox', { name: 'Class / Batch' })
    expect(
      within(batch)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['All Classes / Batches', '2024 batch', 'Class 12'])
    await user.type(screen.getByRole('searchbox', { name: 'Search schema by name' }), 'matrix')
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/schemas').some((c) => c.query.get('q') === 'matrix')).toBe(
        true,
      ),
    )
    await user.selectOptions(batch, 'Class 12')
    await waitFor(() => {
      const last = mock.callsTo('GET /api/v1/schemas').at(-1)
      expect(last?.query.get('target_batch')).toBe('Class 12')
      expect(last?.query.get('q')).toBe('matrix')
    })
  })

  it('says when there are no schemas, and when none matches the filter', async () => {
    session({ 'GET /api/v1/schemas': { json: { items: [], batches: [] } } })
    renderApp('/sme/schemas')
    expect(await screen.findByRole('heading', { name: 'No schemas yet' })).toBeInTheDocument()
    cleanup()
    vi.unstubAllGlobals()
    session({ 'GET /api/v1/schemas': { json: { items: [], batches: ['Class 12'] } } })
    const user = userEvent.setup()
    renderApp('/sme/schemas?q=zzz')
    expect(await screen.findByRole('heading', { name: 'No schema matches' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear the filter' }))
    expect(await screen.findByRole('heading', { name: 'No schemas yet' })).toBeInTheDocument()
  })

  it('adds a schema from the dialog and opens it in the designer', async () => {
    let created: BlueprintDocument | null = null
    const mock = session({
      'POST /api/v1/blueprints': (call) => {
        created = call.body as BlueprintDocument
        return { status: 201, json: blueprintOf('schema-9', created) }
      },
      'GET /api/v1/blueprints/schema-9': () => ({
        json: blueprintOf('schema-9', created ?? documentOf()),
      }),
    })
    const user = userEvent.setup()
    renderApp('/sme/schemas')
    await cards()
    await user.click(screen.getByRole('button', { name: 'Add New Schema' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add New Schema Blueprint' })
    const save = within(dialog).getByRole('button', { name: 'Save Schema' })
    expect(save).toBeDisabled()
    await user.type(within(dialog).getByRole('textbox', { name: 'Schema Name' }), 'Optics Matrix')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Subject' }), physics.id)
    await user.type(
      within(dialog).getByRole('combobox', { name: 'Target Class / Batch' }),
      'Class 12',
    )
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Rubric Weightage Description' }),
      '50/50',
    )
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Save Schema' }),
    )
    await waitFor(() => expect(mock.callsTo('POST /api/v1/blueprints')).toHaveLength(1))
    expect(created).toMatchObject({
      schema_version: '1.2',
      kind: 'schema',
      title: 'Optics Matrix',
      subject_id: physics.id,
      course_code: physics.code,
      exam_code: '',
      target_batch: 'Class 12',
      weightage_note: '50/50',
      from_schema: null,
    })
    expect(await screen.findByRole('heading', { name: 'Schema Blueprint' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toHaveTextContent(
      'Manage Schema › Optics Matrix',
    )
  })

  it('clones a schema under a new name, batch and weightage', async () => {
    const mock = session({
      'POST /api/v1/blueprints/schema-1/clone': (call) => ({
        status: 201,
        json: blueprintOf(
          'schema-3',
          documentOf({ title: (call.body as { title: string }).title }),
        ),
      }),
    })
    const user = userEvent.setup()
    renderApp('/sme/schemas')
    await cards()
    await user.click(screen.getByRole('button', { name: 'Clone Midterm Matrix' }))
    const dialog = await screen.findByRole('dialog', { name: 'Clone Midterm Matrix' })
    const name = within(dialog).getByRole('textbox', { name: 'Schema Name' })
    expect(name).toHaveValue('Midterm Matrix (copy)')
    expect(within(dialog).queryByRole('combobox', { name: 'Subject' })).not.toBeInTheDocument()
    await user.clear(name)
    await user.type(name, 'Matrix B')
    await user.click(within(dialog).getByRole('button', { name: 'Save copy' }))
    await waitFor(() =>
      expect(mock.callsTo('POST /api/v1/blueprints/schema-1/clone')).toHaveLength(1),
    )
    expect(mock.callsTo('POST /api/v1/blueprints/schema-1/clone')[0]?.body).toEqual({
      title: 'Matrix B',
      target_batch: 'Class 12',
      weightage_note: '40% conceptual, 60% analytical',
    })
    expect(await screen.findByText('Matrix B made.')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('generates a question paper from a schema and opens it in the paper editor', async () => {
    const paper = blueprintOf(
      'paper-1',
      documentOf({ kind: 'paper', title: 'Midterm Matrix paper', from_schema: 'schema-1' }),
    )
    const mock = session({
      'POST /api/v1/blueprints/schema-1/paper': { status: 201, json: paper },
      'GET /api/v1/blueprints/paper-1': { json: paper },
      'GET /api/v1/blueprints': { json: [paper] },
      'GET /api/v1/papers': {
        json: { items: [], total: 0, limit: 12, offset: 0, descriptors: [] },
      },
    })
    const user = userEvent.setup()
    renderApp('/sme/schemas')
    await cards()
    await user.click(
      screen.getByRole('button', { name: 'Generate Question Paper from Midterm Matrix' }),
    )
    expect(await screen.findByRole('heading', { name: 'Exam Blueprint' })).toBeInTheDocument()
    // P34: the paper opens in Manage Question Papers, not the older Schema Designer.
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toHaveTextContent(
      'Manage Question Papers',
    )
    expect(mock.callsTo('POST /api/v1/blueprints/schema-1/paper')).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue(
      'Midterm Matrix paper',
    )
    expect(screen.getByRole('textbox', { name: 'Exam Code' })).toBeInTheDocument()
  })

  it('deletes a schema after the server allows it', async () => {
    const mock = session({
      'GET /api/v1/blueprints/schema-2/deletion': { json: { allowed: true, blockers: [] } },
      'DELETE /api/v1/blueprints/schema-2': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/sme/schemas')
    await cards()
    await user.click(screen.getByRole('button', { name: 'Delete Board Rubric' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete this schema?' })
    expect(dialog).toHaveTextContent('papers generated from it stay')
    await user.click(await within(dialog).findByRole('button', { name: 'Delete permanently' }))
    await waitFor(() => expect(mock.callsTo('DELETE /api/v1/blueprints/schema-2')).toHaveLength(1))
    expect(await screen.findByText('Board Rubric deleted.')).toBeInTheDocument()
  })
})
