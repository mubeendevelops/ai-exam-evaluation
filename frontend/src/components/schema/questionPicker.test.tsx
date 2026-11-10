import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BlueprintDocument } from '../../lib/blueprint'
import { physics } from '../../test/adminFixtures'
import { detail, summary } from '../../test/qnaFixtures'
import { renderApp } from '../../test/render'
import { blueprintOf, paperDocumentOf, verdictOf } from '../../test/schemaFixtures'
import { smeSession } from '../../test/smeFixtures'
import { sameMarks } from './QuestionPicker'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const two = summary({ id: 'q-2', code: 'PHY-002', text: 'State Ohm’s law.', max_marks: 2 })
const four = summary({ id: 'q-4', code: 'PHY-004', text: 'Derive the lens formula.', max_marks: 4 })
const paper = blueprintOf('paper-1', paperDocumentOf(), 2)

function session(extra: Parameters<typeof smeSession>[0] = {}) {
  return smeSession({
    'GET /api/v1/blueprints/paper-1': { json: paper },
    'POST /api/v1/blueprints/validate': verdictOf,
    'GET /api/v1/papers': {
      json: { items: [], total: 0, limit: 12, offset: 0, descriptors: [] },
    },
    'GET /api/v1/questions': (call) => {
      const keyword = call.query.get('keyword') ?? ''
      const items = [two, four].filter((q) => q.text.toLowerCase().includes(keyword.toLowerCase()))
      return { json: { items, total: items.length, limit: 10, offset: 0 } }
    },
    'GET /api/v1/questions/q-2': { json: detail({ ...two }) },
    'GET /api/v1/question-topics': { json: ['Mechanics'] },
    ...extra,
  })
}

async function openSlot(user: ReturnType<typeof userEvent.setup>) {
  renderApp('/sme/papers/paper-1')
  await user.click(await screen.findByRole('button', { name: 'Show details of Section 1 item 1' }))
}

function shownJson(): BlueprintDocument {
  return JSON.parse(screen.getByTestId('schema-json').textContent ?? '{}') as BlueprintDocument
}

describe('the slot → question picker (P34, O22/O26)', () => {
  it('searches the paper’s subject and links a question with the slot’s marks', async () => {
    const mock = session()
    const user = userEvent.setup()
    await openSlot(user)
    expect(screen.getByText('Not linked yet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Link the question of Section 1 item 1' }))
    const dialog = await screen.findByRole('dialog', { name: 'Question for Section 1 item 1' })
    const rows = within(await within(dialog).findByRole('list', { name: 'Matching questions' }))
    // A 4-mark question cannot fill a 2-mark slot (the server checks it too).
    expect(rows.getByRole('button', { name: 'Link PHY-004' })).toBeDisabled()
    expect(dialog).toHaveTextContent('4 marks: this slot carries 2.')
    await user.type(within(dialog).getByRole('searchbox'), 'ohm')
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/questions').at(-1)?.query.get('keyword')).toBe('ohm'),
    )
    const last = mock.callsTo('GET /api/v1/questions').at(-1)
    expect(last?.query.get('subject_id')).toBe(physics.id)
    await user.click(within(dialog).getByRole('button', { name: 'Link PHY-002' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByText('PHY-002')).toBeInTheDocument()
    expect(screen.getByText(/State Ohm’s law/)).toBeInTheDocument()
    const item = shownJson().sections[0]?.items[0]
    expect(item).toMatchObject({ question_id: 'q-2' })
    // Unlink puts it back.
    await user.click(
      screen.getByRole('button', { name: 'Unlink the question of Section 1 item 1' }),
    )
    expect(screen.getByText('Not linked yet.')).toBeInTheDocument()
  })

  it('writes a new question for the slot in the editor and comes back with it linked', async () => {
    const made = detail({ id: 'q-new', code: 'PHY-NEW', text: 'Define work.', max_marks: 2 })
    const mock = session({
      'POST /api/v1/questions': { status: 201, json: made },
      'GET /api/v1/questions/q-new': { json: made },
    })
    const user = userEvent.setup()
    await openSlot(user)
    // An unsaved edit travels there and back.
    const title = screen.getByRole('textbox', { name: 'Exam Title / Header' })
    await user.clear(title)
    await user.type(title, 'Edited on the way')
    await user.click(screen.getByRole('button', { name: 'Link the question of Section 1 item 1' }))
    const search = await screen.findByRole('dialog', { name: 'Question for Section 1 item 1' })
    await user.click(within(search).getByRole('button', { name: 'New question' }))
    // The editor is a page, with the slot's subject and marks filled in.
    const form = await screen.findByRole('form', { name: 'New question' })
    expect(within(form).getByRole('textbox', { name: 'Max marks' })).toHaveValue('2')
    expect(within(form).getByRole('combobox', { name: 'Subject' })).toHaveValue(physics.id)
    expect(screen.getByText(/links the new question to its slot \(2 marks\)/)).toBeVisible()
    await user.type(
      within(form).getByRole('textbox', { name: 'Question Title / Text' }),
      'Define work.',
    )
    await user.type(within(form).getByRole('textbox', { name: /Question code/ }), 'PHY-NEW')
    await user.click(within(form).getByRole('button', { name: 'Save Question' }))
    await waitFor(() => expect(mock.callsTo('POST /api/v1/questions')).toHaveLength(1))
    expect(mock.callsTo('POST /api/v1/questions')[0]?.body).toMatchObject({
      subject_id: physics.id,
      max_marks: 2,
    })
    // Back on the paper: linked, with the unsaved edit restored and still unsaved.
    expect(await screen.findByText('PHY-NEW')).toBeInTheDocument()
    expect(shownJson().sections[0]?.items[0]).toMatchObject({ question_id: 'q-new' })
    expect(screen.getByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue(
      'Edited on the way',
    )
    expect(screen.getByText(/Version 2 · unsaved changes/)).toBeInTheDocument()
    expect(
      await screen.findByText('Question linked. Save the paper to keep the link.'),
    ).toBeVisible()
    // The draft is used up: reloading the paper starts from what was saved.
    expect(globalThis.sessionStorage.length).toBe(0)
  })

  it('edits the linked question in the editor and comes back to the paper', async () => {
    const linked = paperDocumentOf()
    const sections = linked.sections.map((s, i) =>
      i === 0
        ? {
            ...s,
            items: s.items.map((it, j) =>
              j === 0 && it.type === 'question' ? { ...it, question_id: 'q-2' } : it,
            ),
          }
        : s,
    )
    session({
      'GET /api/v1/blueprints/paper-1': {
        json: blueprintOf('paper-1', { ...linked, sections }, 2),
      },
    })
    const user = userEvent.setup()
    await openSlot(user)
    await screen.findByText('PHY-002')
    await user.click(screen.getByRole('button', { name: 'Edit the question of Section 1 item 1' }))
    expect(await screen.findByRole('form', { name: 'Edit question' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to the question paper' }))
    expect(await screen.findByRole('button', { name: 'Save paper' })).toBeVisible()
    expect(await screen.findByText('PHY-002')).toBeInTheDocument()
  })

  it('needs the paper’s subject first', async () => {
    session({
      'GET /api/v1/blueprints/paper-1': {
        json: blueprintOf('paper-1', paperDocumentOf({ subject_id: '' }), 2),
      },
    })
    const user = userEvent.setup()
    await openSlot(user)
    expect(screen.getByText("Pick the paper's subject first.")).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Link the question of Section 1 item 1' }),
    ).toBeDisabled()
  })

  it('compares marks as numbers', () => {
    expect(sameMarks('2', 2)).toBe(true)
    expect(sameMarks('2.0', 2)).toBe(true)
    expect(sameMarks('2.5', 2.5)).toBe(true)
    expect(sameMarks('', 0)).toBe(false)
    expect(sameMarks('x', 2)).toBe(false)
    expect(sameMarks('4', 2)).toBe(false)
  })
})
