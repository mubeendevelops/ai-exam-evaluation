import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlueprintDocument } from '../../lib/blueprint'
import { renderApp } from '../../test/render'
import { blueprintOf, cardOf, documentOf, verdictOf } from '../../test/schemaFixtures'
import { smeSession } from '../../test/smeFixtures'

beforeEach(() => globalThis.sessionStorage.clear())
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
    expect(screen.getByRole('link', { name: 'Open it in the Schema Designer' })).toHaveAttribute(
      'href',
      '/schema?blueprint=paper-1',
    )
    cleanup()
    vi.unstubAllGlobals()
    session({ 'GET /api/v1/blueprints/gone': { status: 404, json: { detail: 'not found' } } })
    renderApp('/sme/schemas/gone')
    expect(await screen.findByRole('heading', { name: 'Schema not found' })).toBeInTheDocument()
  })
})

describe('the designer in paper mode (/schema?blueprint=)', () => {
  it('loads a saved paper with its exam code, links and 1.2 fields (O20)', async () => {
    session()
    const user = userEvent.setup()
    renderApp('/schema?blueprint=paper-1')
    expect(await screen.findByRole('heading', { name: 'Exam Blueprint' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue(
      'Physics mid-term',
    )
    expect(screen.getByRole('textbox', { name: 'Exam Code' })).toHaveValue('PHY-MID')
    expect(screen.getByRole('textbox', { name: 'Descriptor' })).toHaveValue('Mid-term')
    expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Chapters 1-4')
    await user.click(screen.getByRole('button', { name: 'Show details of Section 1 item 1' }))
    expect(screen.getByRole('textbox', { name: 'Section 1 item 1 question ID' })).toHaveValue(LINK)
    expect(shownJson()).toMatchObject({ kind: 'paper', from_schema: 'schema-1' })
    expect(screen.getByText('Version 3 · saved')).toBeInTheDocument()
    // The saved list marks the open one and offers Open for the others.
    const saved = screen.getByRole('region', { name: 'Saved papers' })
    expect(within(saved).getByText('Open above')).toBeInTheDocument()
    expect(within(saved).getByRole('link', { name: 'Open Midterm Matrix' })).toHaveAttribute(
      'href',
      '/schema?blueprint=schema-1',
    )
  })

  it('saves the next version, or a new blueprint that it then opens', async () => {
    const mock = session({
      'PUT /api/v1/blueprints/paper-1': (call) => ({
        json: blueprintOf('paper-1', call.body as BlueprintDocument, 4),
      }),
      'POST /api/v1/blueprints': (call) => ({
        status: 201,
        json: blueprintOf('paper-2', call.body as BlueprintDocument),
      }),
      'GET /api/v1/blueprints/paper-2': {
        json: blueprintOf('paper-2', { ...paperDocument, title: 'Physics re-sit' }),
      },
    })
    const user = userEvent.setup()
    renderApp('/schema?blueprint=paper-1')
    const code = await screen.findByRole('textbox', { name: 'Exam Code' })
    await user.clear(code)
    await user.type(code, 'PHY-MID-2')
    const save = screen.getByRole('button', { name: 'Save' })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(mock.callsTo('PUT /api/v1/blueprints/paper-1')).toHaveLength(1))
    expect(mock.callsTo('PUT /api/v1/blueprints/paper-1')[0]?.body).toMatchObject({
      exam_code: 'PHY-MID-2',
      from_schema: 'schema-1',
    })
    expect(await screen.findByText('Version 4 · saved')).toBeInTheDocument()

    const title = screen.getByRole('textbox', { name: 'Exam Title / Header' })
    await user.clear(title)
    await user.type(title, 'Physics re-sit')
    const asNew = screen.getByRole('button', { name: 'Save as new' })
    await waitFor(() => expect(asNew).toBeEnabled())
    await user.click(asNew)
    await waitFor(() => expect(mock.callsTo('POST /api/v1/blueprints')).toHaveLength(1))
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/blueprints/paper-2').length).toBeGreaterThan(0),
    )
    expect(await screen.findByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue(
      'Physics re-sit',
    )
  })

  it('saves a new blueprint from the draft and clears the draft', async () => {
    const mock = session({
      'POST /api/v1/blueprints': (call) => ({
        status: 201,
        json: blueprintOf('paper-5', call.body as BlueprintDocument),
      }),
      'GET /api/v1/blueprints/paper-5': {
        json: blueprintOf('paper-5', { ...paperDocument, title: 'Fresh paper' }),
      },
    })
    const user = userEvent.setup()
    renderApp('/schema')
    const title = await screen.findByRole('textbox', { name: 'Exam Title / Header' })
    await user.type(title, 'Fresh paper')
    const save = screen.getByRole('button', { name: 'Save blueprint' })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(mock.callsTo('POST /api/v1/blueprints')).toHaveLength(1))
    expect(mock.callsTo('POST /api/v1/blueprints')[0]?.body).toMatchObject({
      kind: 'paper',
      title: 'Fresh paper',
    })
    expect(await screen.findByText('Version 1 · saved')).toBeInTheDocument()
    expect(globalThis.sessionStorage.getItem('tarn.schema-designer.draft')).toBeNull()
  })
})
