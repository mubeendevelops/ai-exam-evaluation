import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BlueprintDocument } from '../../lib/blueprint'
import { renderApp } from '../../test/render'
import { blueprintOf, cardOf, documentOf, verdictOf } from '../../test/schemaFixtures'
import { detail } from '../../test/qnaFixtures'
import { smeSession } from '../../test/smeFixtures'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const LINK = '11111111-1111-4111-8111-111111111111'
const schema = blueprintOf('schema-1', documentOf(), 2)
const paperDocument = documentOf({
  kind: 'paper',
  title: 'Physics mid-term',
  exam_code: 'PHY-MID',
  descriptor: 'Mid-term',
  description: 'Chapters 1-4',
  from_schema: 'schema-1',
  sections: documentOf().sections.map((s) => ({
    ...s,
    items: s.items.map((item, i) => (i === 0 ? { ...item, question_id: LINK } : item)),
  })),
})
const paper = blueprintOf('paper-1', paperDocument, 3)

function session(extra: Parameters<typeof smeSession>[0] = {}) {
  return smeSession({
    'GET /api/v1/blueprints/schema-1': { json: schema },
    'GET /api/v1/blueprints/paper-1': { json: paper },
    'GET /api/v1/blueprints': { json: [schema, paper] },
    'GET /api/v1/schemas': { json: { items: [cardOf()], batches: ['2024 batch', 'Class 12'] } },
    'POST /api/v1/blueprints/validate': verdictOf,
    'GET /api/v1/papers': {
      json: { items: [], total: 0, limit: 12, offset: 0, descriptors: ['Board Final', 'Mid-term'] },
    },
    ...extra,
  })
}

function shownJson(): BlueprintDocument {
  return JSON.parse(screen.getByTestId('schema-json').textContent ?? '{}') as BlueprintDocument
}

describe('the designer in schema mode (/sme/schemas/:id)', () => {
  it('loads the saved schema without exam code or question links', async () => {
    session()
    const user = userEvent.setup()
    renderApp('/sme/schemas/schema-1')
    expect(await screen.findByRole('heading', { name: 'Schema Blueprint' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Schema Name' })).toHaveValue('Midterm Matrix')
    expect(screen.getByLabelText('Target Class / Batch')).toHaveValue('Class 12')
    expect(screen.getByRole('textbox', { name: 'Rubric Weightage Description' })).toHaveValue(
      '40% conceptual, 60% analytical',
    )
    expect(screen.queryByRole('textbox', { name: 'Exam Code' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Descriptor' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show details of Section 1 item 1' }))
    expect(
      screen.queryByRole('textbox', { name: 'Section 1 item 1 question ID' }),
    ).not.toBeInTheDocument()
    expect(shownJson()).toMatchObject({ kind: 'schema', exam_code: '', from_schema: null })
    expect(screen.getByText('Version 2 · saved')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save schema' })).toBeDisabled()
    expect(await screen.findByText(/A schema leaves its questions unlinked/)).toBeInTheDocument()
  })

  it('saves a change as the next version', async () => {
    const mock = session({
      'PUT /api/v1/blueprints/schema-1': (call) => ({
        json: blueprintOf('schema-1', call.body as BlueprintDocument, 3),
      }),
    })
    const user = userEvent.setup()
    renderApp('/sme/schemas/schema-1')
    const name = await screen.findByRole('textbox', { name: 'Schema Name' })
    await user.clear(name)
    await user.type(name, 'Midterm Matrix B')
    expect(screen.getByText('Version 2 · unsaved changes')).toBeInTheDocument()
    // Generating copies the saved version: not while there are unsaved changes.
    expect(screen.getByRole('button', { name: 'Generate Question Paper' })).toBeDisabled()
    const save = screen.getByRole('button', { name: 'Save schema' })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(mock.callsTo('PUT /api/v1/blueprints/schema-1')).toHaveLength(1))
    expect(mock.callsTo('PUT /api/v1/blueprints/schema-1')[0]?.body).toMatchObject({
      kind: 'schema',
      title: 'Midterm Matrix B',
      target_batch: 'Class 12',
    })
    expect(await screen.findByText('Version 3 · saved')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate Question Paper' })).toBeEnabled()
  })

  it('sends a paper to the paper editor and a missing one back to the list', async () => {
    session({ 'GET /api/v1/blueprints/gone': { status: 404, json: { detail: 'not found' } } })
    renderApp('/sme/schemas/paper-1')
    expect(
      await screen.findByRole('heading', { name: 'This is a question paper' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open it in Manage Question Papers' })).toHaveAttribute(
      'href',
      '/sme/papers/paper-1',
    )
    cleanup()
    vi.unstubAllGlobals()
    session({ 'GET /api/v1/blueprints/gone': { status: 404, json: { detail: 'not found' } } })
    renderApp('/sme/schemas/gone')
    expect(await screen.findByRole('heading', { name: 'Schema not found' })).toBeInTheDocument()
  })
})

describe('the designer in paper mode (/sme/papers/:id, P34)', () => {
  const linked = detail({ id: LINK, code: 'PHY-Q1', text: 'State Lenz’s law.', max_marks: 2 })

  it('loads a saved paper with its exam code, linked question and 1.2 fields', async () => {
    session({ [`GET /api/v1/questions/${LINK}`]: { json: linked } })
    const user = userEvent.setup()
    renderApp('/sme/papers/paper-1')
    expect(await screen.findByRole('heading', { name: 'Exam Blueprint' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue(
      'Physics mid-term',
    )
    expect(screen.getByRole('textbox', { name: 'Exam Code' })).toHaveValue('PHY-MID')
    expect(await screen.findByRole('combobox', { name: 'Descriptor' })).toHaveValue('Mid-term')
    expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Chapters 1-4')
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toHaveTextContent(
      'Manage Question Papers › Physics mid-term',
    )
    await user.click(screen.getByRole('button', { name: 'Show details of Section 1 item 1' }))
    expect(await screen.findByText('PHY-Q1')).toBeInTheDocument()
    expect(screen.getByText(/State Lenz’s law/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Change the question of Section 1 item 1' }),
    ).toBeEnabled()
    expect(shownJson()).toMatchObject({ kind: 'paper', from_schema: 'schema-1' })
    expect(screen.getByText('Version 3 · saved')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('saves the next version; Preview waits for the save', async () => {
    const mock = session({
      [`GET /api/v1/questions/${LINK}`]: { json: linked },
      'PUT /api/v1/blueprints/paper-1': (call) => ({
        json: blueprintOf('paper-1', call.body as BlueprintDocument, 4),
      }),
    })
    const user = userEvent.setup()
    renderApp('/sme/papers/paper-1')
    const code = await screen.findByRole('textbox', { name: 'Exam Code' })
    await user.clear(code)
    await user.type(code, 'PHY-MID-2')
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled()
    const save = screen.getByRole('button', { name: 'Save paper' })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(mock.callsTo('PUT /api/v1/blueprints/paper-1')).toHaveLength(1))
    expect(mock.callsTo('PUT /api/v1/blueprints/paper-1')[0]?.body).toMatchObject({
      exam_code: 'PHY-MID-2',
      from_schema: 'schema-1',
    })
    expect(await screen.findByText('Version 4 · saved')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled()
  })

  it('says when the structure is locked and keeps the links read-only (D222)', async () => {
    session({
      'GET /api/v1/blueprints/paper-1': { json: blueprintOf('paper-1', paperDocument, 3, true) },
      [`GET /api/v1/questions/${LINK}`]: { json: linked },
    })
    const user = userEvent.setup()
    renderApp('/sme/papers/paper-1')
    expect(await screen.findByRole('note')).toHaveTextContent(/structure is locked/)
    await user.click(screen.getByRole('button', { name: 'Show details of Section 1 item 1' }))
    expect(await screen.findByText('PHY-Q1')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Change the question of Section 1 item 1' }),
    ).not.toBeInTheDocument()
  })

  it('sends a schema to Manage Schema and a missing paper back to the list', async () => {
    session({ 'GET /api/v1/blueprints/gone': { status: 404, json: { detail: 'not found' } } })
    renderApp('/sme/papers/schema-1')
    expect(await screen.findByRole('heading', { name: 'This is a schema' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open it in Manage Schema' })).toHaveAttribute(
      'href',
      '/sme/schemas/schema-1',
    )
    cleanup()
    vi.unstubAllGlobals()
    session({ 'GET /api/v1/blueprints/gone': { status: 404, json: { detail: 'not found' } } })
    renderApp('/sme/papers/gone')
    expect(await screen.findByRole('heading', { name: 'Paper not found' })).toBeInTheDocument()
  })

  it('sends the old Schema Designer address to Manage Question Papers', async () => {
    session()
    renderApp('/schema')
    expect(
      await screen.findByRole('heading', { name: 'Manage Question Papers', level: 2 }),
    ).toBeInTheDocument()
  })
})
