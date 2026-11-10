import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { healthy, meOf, tokenOf } from '../../test/fixtures'
import { graph } from '../../test/evaluateFixtures'
import { mockApi, type Call } from '../../test/mockApi'
import { detail, SUBJECT_ID, summary, type Detail } from '../../test/qnaFixtures'
import { renderApp } from '../../test/render'

beforeEach(() => globalThis.sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const physics = {
  id: SUBJECT_ID,
  code: 'PHY',
  name: 'Physics',
}
const maths = { ...physics, id: '44444444-4444-4444-8444-444444444444', code: 'MAT', name: 'Maths' }

function bank(extra: Parameters<typeof mockApi>[0] = {}, questions = [summary()]) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf() },
    'GET /api/v1/auth/me': { json: meOf() },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/subjects': { json: [physics, maths] },
    'GET /api/v1/question-topics': { json: ['AC Circuits', 'Electromagnetism'] },
    'GET /api/v1/questions': () => ({
      json: { items: questions, total: questions.length, limit: 12, offset: 0 },
    }),
    ...extra,
  })
}

/** The college's shared admin account: SMEs author content; the admin never opens it (D167). */
const AS_ADMIN = {
  'POST /api/v1/auth/refresh': { json: tokenOf({ role: 'admin' }) },
  'GET /api/v1/auth/me': { json: meOf({ role: 'admin' }) },
}

function opened(question: Detail = detail(), extra: Parameters<typeof mockApi>[0] = {}) {
  return bank(
    {
      [`GET /api/v1/questions/${question.id}`]: { json: question },
      [`GET /api/v1/questions/${question.id}/usage`]: { json: { papers: [] } },
      ...extra,
    },
    [summary({ id: question.id })],
  )
}

async function openFirst() {
  const user = userEvent.setup()
  renderApp('/sme/questions')
  await user.click(await screen.findByRole('button', { name: 'Open PHY-Q1' }))
  await screen.findByRole('region', { name: 'Benchmark answers' })
  return user
}

describe('repository browser', () => {
  it('shows question cards with code, difficulty, marks, subject, keys and version', async () => {
    bank({}, [
      summary(),
      summary({
        id: 'q-2',
        code: 'MAT-Q1',
        text: 'Integrate x dx.',
        difficulty: 'hard',
        max_marks: 1,
        subject_name: 'Maths',
        category: '',
        key_count: 0,
      }),
    ])
    renderApp('/sme/questions')
    const first = await screen.findByRole('article', { name: 'Question PHY-Q1' })
    expect(within(first).getByText('Easy')).toBeVisible()
    expect(within(first).getByText('4 Marks')).toBeVisible()
    expect(within(first).getByText('Physics')).toBeVisible()
    expect(within(first).getByText('Electromagnetism')).toBeVisible()
    expect(within(first).getByText('2 reference keys')).toBeVisible()
    expect(within(first).getByText('Version 1')).toBeVisible()

    const second = screen.getByRole('article', { name: 'Question MAT-Q1' })
    expect(within(second).getByText('Hard')).toBeVisible()
    expect(within(second).getByText('1 Mark')).toBeVisible()
    expect(within(second).getByText('0 reference keys')).toBeVisible()
    expect(screen.getByText(/Showing/)).toHaveTextContent('Showing 2 of 2 Questions')
  })

  it('sends every filter to the server', async () => {
    const mock = bank()
    const user = userEvent.setup()
    renderApp('/sme/questions')
    await screen.findByRole('article', { name: 'Question PHY-Q1' })
    const last = (): Call['query'] => mock.callsTo('GET /api/v1/questions').at(-1)!.query

    await user.selectOptions(screen.getByRole('combobox', { name: /Subject/ }), SUBJECT_ID)
    await waitFor(() => expect(last().get('subject_id')).toBe(SUBJECT_ID))
    await user.selectOptions(screen.getByRole('combobox', { name: /Topic/ }), 'AC Circuits')
    await waitFor(() => expect(last().get('topic')).toBe('AC Circuits'))
    await user.selectOptions(screen.getByRole('combobox', { name: /Difficulty/ }), 'hard')
    await waitFor(() => expect(last().get('difficulty')).toBe('hard'))
    expect(screen.queryByRole('checkbox', { name: 'My college only' })).toBeNull()
    await user.type(screen.getByRole('searchbox', { name: 'Search questions' }), 'lenz')
    await waitFor(() => expect(last().get('keyword')).toBe('lenz'))
    await user.type(screen.getByRole('searchbox', { name: 'Code' }), 'phy-q')
    await waitFor(() => expect(last().get('code')).toBe('phy-q'))
    expect(Object.fromEntries(last())).toMatchObject({
      subject_id: SUBJECT_ID,
      topic: 'AC Circuits',
      difficulty: 'hard',
      keyword: 'lenz',
      code: 'phy-q',
      limit: '12',
      offset: '0',
    })
    // the topic list follows the chosen subject
    expect(
      mock
        .callsTo('GET /api/v1/question-topics')
        .some((c) => c.query.get('subject_id') === SUBJECT_ID),
    ).toBe(true)
  })

  it('says when nothing matches, and offers the first question when the bank is empty', async () => {
    bank({}, [])
    const user = userEvent.setup()
    renderApp('/sme/questions')
    expect(await screen.findByText('The question bank is empty')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Add the first question' }))
    expect(await screen.findByRole('form', { name: 'New question' })).toBeVisible()
  })

  it('pages through long results', async () => {
    const mock = mockApi({
      'POST /api/v1/auth/refresh': { json: tokenOf() },
      'GET /api/v1/auth/me': { json: meOf() },
      'GET /api/v1/health': { json: healthy },
      'GET /api/v1/subjects': { json: [] },
      'GET /api/v1/question-topics': { json: [] },
      'GET /api/v1/questions': (call) => ({
        json: {
          items: [summary({ code: `Q-${call.query.get('offset')}` })],
          total: 30,
          limit: 12,
          offset: Number(call.query.get('offset')),
        },
      }),
    })
    const user = userEvent.setup()
    renderApp('/sme/questions')
    await screen.findByRole('article', { name: 'Question Q-0' })
    expect(screen.getByText('Page 1 of 3')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('article', { name: 'Question Q-12' })
    expect(mock.callsTo('GET /api/v1/questions').at(-1)?.query.get('offset')).toBe('12')
  })
})

describe('question detail', () => {
  it('shows the question form with its rubric, the benchmark key, glossary and files', async () => {
    opened()
    await openFirst()
    const keys = screen.getByRole('region', { name: 'Benchmark answers' })
    expect(within(keys).getByText(/induced emf opposes the change of flux/)).toBeVisible()
    const form = screen.getByRole('form', { name: 'Edit question' })
    expect(within(form).getByRole('textbox', { name: 'Question Title / Text' })).toHaveValue(
      'State Lenz’s law.',
    )
    expect(within(form).getByRole('textbox', { name: 'Question code' })).toHaveValue('PHY-Q1')
    const rubric = within(form).getByRole('region', { name: 'Rubric' })
    expect(within(rubric).getByRole('textbox', { name: 'Criterion 1 label' })).toHaveValue(
      'Names the effect',
    )
    expect(within(rubric).getByRole('status')).toHaveTextContent('Weights add up to 4 of 4 marks.')
    const glossary = screen.getByRole('region', { name: 'Glossary' })
    expect(within(glossary).getByText('induced emf')).toBeVisible()
    expect(within(glossary).getByText('coil')).toBeVisible()
    const files = screen.getByRole('region', { name: 'Attached files' })
    expect(within(files).getByText('key.pdf')).toBeVisible()
    expect(within(files).getByText('2 KB')).toBeVisible()
    expect(within(files).getByText('lenz')).toBeVisible()
    expect(within(files).getByText('0 nodes · 0 edges')).toBeVisible()
  })

  it('labels a synthetic key as dev only, and leaves a faculty key unlabelled', async () => {
    const key = (id: string, text: string, synthetic: boolean) => ({
      id,
      version: 1,
      text,
      guidance_only: false,
      synthetic,
    })
    opened({
      ...detail(),
      reference_answers: [
        key('a-1', 'Faculty key text.', false),
        key('a-2', 'Written for development.', true),
      ],
    })
    await openFirst()
    const keys = screen.getByRole('region', { name: 'Benchmark answers' })
    const label = within(keys).getByText('SYNTHETIC – dev only, needs teacher validation')
    const synthetic = within(keys).getByText('Written for development.').parentElement
    const faculty = within(keys).getByText('Faculty key text.').parentElement
    expect(synthetic).toContainElement(label)
    expect(faculty).not.toHaveTextContent('SYNTHETIC')
  })

  it('offers Save and the upload actions on one page, and no modal editors for the question', async () => {
    opened()
    await openFirst()
    expect(screen.getByRole('button', { name: 'Save Question' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Upload Answer Key' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Upload reference diagram' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Edit question' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Edit rubric' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Copy to my college' })).toBeNull()
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('is not for the admin account: it is sent to the admin page (D167, P24)', async () => {
    opened(detail(), AS_ADMIN)
    renderApp('/sme/questions')
    expect(await screen.findByRole('heading', { name: 'Dashboard Overview' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open PHY-Q1' })).toBeNull()
  })

  it('flags weights that do not add up', async () => {
    opened()
    const user = await openFirst()
    const weight = screen.getByRole('textbox', { name: 'Criterion 1 weight' })
    await user.clear(weight)
    await user.type(weight, '1')
    expect(
      within(screen.getByRole('region', { name: 'Rubric' })).getByRole('status'),
    ).toHaveTextContent('Weights add up to 3 of 4 marks: they must match.')
  })

  it('downloads a file through the token', async () => {
    const mock = opened(detail(), {
      'GET /api/v1/questions/q-1/key-files/f-1/content': { body: '%PDF-1.7 synthetic' },
    })
    const saved: string[] = []
    vi.stubGlobal(
      'URL',
      Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }),
    )
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download)
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Download key.pdf' }))
    await waitFor(() => expect(saved).toEqual(['key.pdf']))
    expect(
      mock
        .callsTo('GET /api/v1/questions/q-1/key-files/f-1/content')[0]
        ?.headers.get('authorization'),
    ).toMatch(/^Bearer /)
    vi.restoreAllMocks()
  })

  it('says so for an unknown question', async () => {
    bank({ 'GET /api/v1/questions/nope': { status: 404, json: { detail: 'Not found.' } } })
    renderApp('/sme/questions/nope')
    expect(await screen.findByText('Question not found')).toBeVisible()
  })
})

describe('reference answers and glossary', () => {
  it('adds, edits and removes a reference answer', async () => {
    const mock = opened(detail(), {
      'POST /api/v1/questions/q-1/reference-answers': { status: 201, json: {} },
      'PUT /api/v1/questions/q-1/reference-answers/a-1': { json: {} },
      'DELETE /api/v1/questions/q-1/reference-answers/a-1': { status: 204 },
    })
    const user = await openFirst()

    await user.click(screen.getByRole('button', { name: 'Add reference answer' }))
    const add = screen.getByRole('dialog', { name: 'Add reference answer' })
    await user.type(within(add).getByRole('textbox', { name: 'Answer text' }), 'Use judgement.')
    await user.click(within(add).getByRole('checkbox'))
    await user.click(within(add).getByRole('button', { name: 'Save answer' }))
    await waitFor(() =>
      expect(mock.callsTo('POST /api/v1/questions/q-1/reference-answers')[0]?.body).toEqual({
        text: 'Use judgement.',
        guidance_only: true,
      }),
    )

    await user.click(screen.getByRole('button', { name: 'Edit answer' }))
    const edit = screen.getByRole('dialog', { name: 'Edit reference answer' })
    const box = within(edit).getByRole('textbox', { name: 'Answer text' })
    expect(box).toHaveValue('The induced emf opposes the change of flux.')
    await user.type(box, ' Better.')
    await user.click(within(edit).getByRole('button', { name: 'Save answer' }))
    await waitFor(() =>
      expect(mock.callsTo('PUT /api/v1/questions/q-1/reference-answers/a-1')[0]?.body).toEqual({
        text: 'The induced emf opposes the change of flux. Better.',
        guidance_only: false,
      }),
    )

    await user.click(screen.getByRole('button', { name: 'Remove answer' }))
    expect(await screen.findByText('The reference answer was removed.')).toBeVisible()
  })

  it('saves the glossary terms one per line', async () => {
    const mock = opened(detail(), { 'PUT /api/v1/questions/q-1/glossary': { json: {} } })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Edit glossary' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit glossary' })
    const box = within(dialog).getByRole('textbox', { name: 'Terms, one per line' })
    expect(box).toHaveValue('induced emf') // only the teacher's terms, not the diagram labels
    await user.type(box, '{enter}flux')
    await user.click(within(dialog).getByRole('button', { name: 'Save glossary' }))
    await waitFor(() =>
      expect(mock.callsTo('PUT /api/v1/questions/q-1/glossary')[0]?.body).toEqual({
        terms: ['induced emf', 'flux'],
      }),
    )
  })
})

describe('the question form and the rubric editor', () => {
  async function fillQuestion(user: ReturnType<typeof userEvent.setup>) {
    const dialog = await screen.findByRole('form', { name: 'New question' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Question Title / Text' }), 'Why?')
    await user.selectOptions(
      await within(dialog).findByRole('combobox', { name: 'Subject' }),
      SUBJECT_ID,
    )
    await user.type(within(dialog).getByRole('combobox', { name: 'Topic' }), 'Optics')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Difficulty' }), 'hard')
    await user.type(within(dialog).getByRole('textbox', { name: 'Max marks' }), '6')
    await user.type(within(dialog).getByRole('textbox', { name: 'Question code' }), 'PHY-Q7')
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Reference answer (benchmark)' }),
      'Rayleigh scattering.',
    )
    return dialog
  }

  it('sends the question with difficulty, key and a four-type rubric', async () => {
    const created = detail({ id: 'q-7', code: 'PHY-Q7' })
    const mock = bank({
      'POST /api/v1/questions': { status: 201, json: created },
      'GET /api/v1/questions/q-7': { json: created },
    })
    const user = userEvent.setup()
    renderApp('/sme/questions/new')
    const dialog = await fillQuestion(user)

    await user.click(within(dialog).getByRole('button', { name: 'Add list / keywords criterion' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 1 label' }), 'Names it')
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Criterion 1 items' }),
      'Rayleigh | scattering, Tyndall{enter}wavelength',
    )
    await user.clear(within(dialog).getByRole('textbox', { name: 'Criterion 1 required count' }))
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Criterion 1 required count' }),
      '2',
    )
    await user.clear(within(dialog).getByRole('textbox', { name: 'Criterion 1 weight' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 1 weight' }), '2')

    await user.click(within(dialog).getByRole('button', { name: 'Add numeric value criterion' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 2 label' }), 'Step')
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Criterion 2 expected value' }),
      '2.5',
    )
    await user.clear(within(dialog).getByRole('textbox', { name: 'Criterion 2 tolerance' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 2 tolerance' }), '0.1')
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 2 unit' }), 'V')

    await user.click(
      within(dialog).getByRole('button', { name: 'Add semantic statement criterion' }),
    )
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 3 label' }), 'Why')
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Criterion 3 reference statement' }),
      'Short wavelengths scatter more.',
    )
    // the weights so far: 2 + 4 (what was left) ... the status line follows
    expect(within(dialog).getByRole('status')).toHaveTextContent('Weights add up to')

    await user.click(within(dialog).getByRole('button', { name: 'Save Question' }))
    await waitFor(() => expect(mock.callsTo('POST /api/v1/questions')).toHaveLength(1))
    const sent = mock.callsTo('POST /api/v1/questions')[0]!.body as Record<string, unknown>
    expect(sent).toMatchObject({
      subject_id: SUBJECT_ID,
      code: 'PHY-Q7',
      text: 'Why?',
      max_marks: 6,
      difficulty: 'hard',
      category: 'Optics',
      reference_answer: 'Rayleigh scattering.',
    })
    const criteria = sent.criteria as { type: string; weight: number; params: unknown }[]
    expect(criteria.map((c) => c.type)).toEqual(['list', 'numeric', 'semantic'])
    expect(criteria[0]).toMatchObject({
      label: 'Names it',
      weight: 2,
      params: {
        items: [
          { term: 'Rayleigh', synonyms: ['scattering', 'Tyndall'] },
          { term: 'wavelength', synonyms: [] },
        ],
        required_count: 2,
      },
    })
    expect(criteria[1]!.params).toEqual({ expected: 2.5, tolerance: 0.1, unit: 'V' })
    expect(criteria[2]!.params).toEqual({ reference_statement: 'Short wavelengths scatter more.' })
    // after saving, the new question is open on its own page
    expect(await screen.findByText('Question saved.')).toBeVisible()
    expect(await screen.findByRole('region', { name: 'Benchmark answers' })).toBeVisible()
    expect(screen.getByRole('form', { name: 'Edit question' })).toBeVisible()
  })

  it('shows the weight total live and shares the marks evenly', async () => {
    bank()
    const user = userEvent.setup()
    renderApp('/sme/questions/new')
    const dialog = await fillQuestion(user)
    expect(within(dialog).getByRole('status')).toHaveTextContent(
      'No criteria: the key is guidance only',
    )
    await user.click(
      within(dialog).getByRole('button', { name: 'Add semantic statement criterion' }),
    )
    await user.click(
      within(dialog).getByRole('button', { name: 'Add semantic statement criterion' }),
    )
    // the first takes all 6 marks, the second the 0 still unassigned
    expect(within(dialog).getByRole('textbox', { name: 'Criterion 1 weight' })).toHaveValue('6')
    expect(within(dialog).getByRole('status')).toHaveTextContent('Weights add up to 6 of 6 marks.')
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 2 weight' }), '1')
    expect(within(dialog).getByRole('status')).toHaveTextContent(
      'Weights add up to 7 of 6 marks: they must match.',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Share the marks evenly' }))
    expect(within(dialog).getByRole('textbox', { name: 'Criterion 1 weight' })).toHaveValue('3')
    expect(within(dialog).getByRole('textbox', { name: 'Criterion 2 weight' })).toHaveValue('3')
    await user.click(within(dialog).getByRole('button', { name: 'Remove Criterion 2' }))
    expect(within(dialog).queryByRole('textbox', { name: 'Criterion 2 weight' })).toBeNull()
  })

  it('shows the server’s reason when the weights do not add up', async () => {
    bank({
      'POST /api/v1/questions': {
        status: 422,
        json: {
          detail:
            'The rubric weights add up to 5 marks, but the question carries 6. Change the weights so that they add up.',
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/sme/questions/new')
    const dialog = await fillQuestion(user)
    await user.click(
      within(dialog).getByRole('button', { name: 'Add semantic statement criterion' }),
    )
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 1 label' }), 'Why')
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Criterion 1 reference statement' }),
      's',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Save Question' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'The rubric weights add up to 5 marks, but the question carries 6.',
    )
    expect(screen.getByRole('form', { name: 'New question' })).toBeVisible() // kept open
  })

  it('edits a question and its rubric together, keeping criterion ids', async () => {
    const mock = opened(detail(), { 'PUT /api/v1/questions/q-1': { json: detail({ version: 2 }) } })
    const user = await openFirst()
    const dialog = screen.getByRole('form', { name: 'Edit question' })
    expect(within(dialog).getByRole('textbox', { name: 'Subject' })).toBeDisabled()
    expect(within(dialog).getByRole('textbox', { name: 'Question code' })).toHaveValue('PHY-Q1')
    expect(within(dialog).getByRole('textbox', { name: 'Criterion 1 items' })).toHaveValue(
      'opposes | resists',
    )
    await user.clear(within(dialog).getByRole('textbox', { name: 'Max marks' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Max marks' }), '5')
    await user.clear(within(dialog).getByRole('textbox', { name: 'Criterion 1 weight' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Criterion 1 weight' }), '3')
    await user.click(within(dialog).getByRole('button', { name: 'Save Question' }))
    await waitFor(() => expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(1))
    const sent = mock.callsTo('PUT /api/v1/questions/q-1')[0]!.body as {
      max_marks: number
      criteria: { id: string; weight: number }[]
    }
    expect(sent.max_marks).toBe(5)
    expect(sent.criteria.map((c) => [c.id, c.weight])).toEqual([
      ['c-1', 3],
      ['c-2', 2],
    ])
  })

  it('edits the rubric in the form, with the question’s diagrams to choose from', async () => {
    const q = detail({
      rubric: {
        ...detail().rubric,
        criteria: [
          {
            version: 1,
            criterion: {
              id: 'c-9',
              type: 'diagram',
              label: 'Nodes',
              weight: 4,
              params: { reference_diagram_id: 'd-1', component: 'nodes' },
            },
          },
        ],
      },
    })
    const mock = opened(q, { 'PUT /api/v1/questions/q-1': { json: q } })
    const user = await openFirst()
    const form = screen.getByRole('form', { name: 'Edit question' })
    const diagram = within(form).getByRole('combobox', { name: 'Criterion 1 reference diagram' })
    expect(diagram).toHaveValue('d-1')
    expect(within(diagram).getByRole('option', { name: 'coil.png' })).toBeInTheDocument()
    await user.selectOptions(
      within(form).getByRole('combobox', { name: 'Criterion 1 compared part' }),
      'edges',
    )
    await user.click(within(form).getByRole('button', { name: 'Save Question' }))
    await waitFor(() => expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(1))
    expect(
      (mock.callsTo('PUT /api/v1/questions/q-1')[0]?.body as { criteria: unknown }).criteria,
    ).toEqual([
      {
        id: 'c-9',
        type: 'diagram',
        label: 'Nodes',
        weight: 4,
        params: { reference_diagram_id: 'd-1', component: 'edges' },
      },
    ])
  })
})

describe('upload dialogs', () => {
  const pdf = () =>
    new File(['%PDF-1.7 synthetic key'], 'lens key.pdf', { type: 'application/pdf' })

  it('shows the no-student-data rule and needs a file and the confirmation', async () => {
    opened()
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Upload Answer Key' }))
    const dialog = screen.getByRole('dialog', { name: 'Upload Answer Key' })
    expect(within(dialog).getByText(/Keys hold no student data/)).toBeVisible()
    expect(within(dialog).getByText(/never upload a student's submission/i)).toBeVisible()
    const upload = within(dialog).getByRole('button', { name: 'Upload answer key' })
    expect(upload).toBeDisabled()

    await user.upload(within(dialog).getByLabelText('Answer key file'), pdf())
    expect(upload).toBeDisabled() // still not confirmed
    await user.click(
      within(dialog).getByRole('checkbox', {
        name: 'I confirm this file contains no student data.',
      }),
    )
    expect(upload).toBeEnabled()
  })

  it('uploads the file with its keywords and the confirmation', async () => {
    const mock = opened(detail(), {
      'POST /api/v1/questions/q-1/key-files': { status: 201, json: {} },
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Upload Answer Key' }))
    const dialog = screen.getByRole('dialog', { name: 'Upload Answer Key' })
    await user.upload(within(dialog).getByLabelText('Answer key file'), pdf())
    await user.type(
      within(dialog).getByRole('textbox', { name: /Keywords/ }),
      'focal length, , convex lens',
    )
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Upload answer key' }))
    expect(await within(dialog).findByText('The answer key was attached.')).toBeVisible()

    const call = mock.callsTo('POST /api/v1/questions/q-1/key-files')[0]!
    expect(call.query.get('filename')).toBe('lens key.pdf')
    expect(call.query.get('confirm_no_student_data')).toBe('true')
    expect(call.query.getAll('keywords')).toEqual(['focal length', 'convex lens'])
    expect(call.headers.get('content-type')).toBe('application/pdf')
    expect(call.body).toBe('%PDF-1.7 synthetic key')
  })

  it('shows why the server refused a file', async () => {
    opened(detail(), {
      'POST /api/v1/questions/q-1/key-files': {
        status: 422,
        json: { detail: 'Only PDF, PNG or JPEG files are accepted; this file is not one.' },
      },
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Upload Answer Key' }))
    const dialog = screen.getByRole('dialog', { name: 'Upload Answer Key' })
    await user.upload(within(dialog).getByLabelText('Answer key file'), pdf())
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Upload answer key' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Only PDF, PNG or JPEG files are accepted',
    )
  })

  it('offers only PDF and image files for keys, and only PNG for diagrams', async () => {
    opened()
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Upload Answer Key' }))
    const key = screen.getByLabelText('Answer key file')
    expect(key).toHaveAttribute('accept', expect.stringContaining('application/pdf'))
    expect(key.getAttribute('accept')).not.toContain('word')
    // a Word file never reaches the form: the picker's accept filter drops it
    await user.upload(
      key,
      new File(['x'], 'key.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      }),
    )
    expect(screen.getByRole('button', { name: 'Upload answer key' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Close' }))

    await user.click(screen.getByRole('button', { name: 'Upload reference diagram' }))
    const diagram = screen.getByLabelText('Reference diagram file')
    expect(diagram.getAttribute('accept')).toBe('.png,image/png')
  })

  it('uploads a reference diagram PNG with the confirmation', async () => {
    const mock = opened(detail(), {
      'POST /api/v1/questions/q-1/diagrams': { status: 201, json: {} },
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Upload reference diagram' }))
    const dialog = screen.getByRole('dialog', { name: 'Upload reference diagram' })
    expect(within(dialog).getByText(/Keys hold no student data/)).toBeVisible()
    await user.upload(
      within(dialog).getByLabelText('Reference diagram file'),
      new File(['\x89PNG'], 'flow.png', { type: 'image/png' }),
    )
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Upload reference diagram' }))
    expect(await within(dialog).findByText(/The reference diagram was attached/)).toBeVisible()
    const call = mock.callsTo('POST /api/v1/questions/q-1/diagrams')[0]!
    expect(call.query.get('filename')).toBe('flow.png')
    expect(call.query.get('confirm_no_student_data')).toBe('true')
    expect(call.headers.get('content-type')).toBe('image/png')
  })
})

describe('reference diagram graph editor', () => {
  const REF = '/api/v1/questions/q-1/diagrams/d-1'
  const referenceGraph = (over: Partial<Record<string, unknown>> = {}) => ({
    diagram_id: 'd-1',
    version: 2,
    kind: 'flowchart',
    recognition: 'recognised',
    content_url: `${REF}/content`,
    graph: graph(),
    ...over,
  })

  function withGraph(teacher = true, extra: Parameters<typeof mockApi>[0] = {}) {
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
    return opened(detail(), {
      ...(teacher ? {} : AS_ADMIN),
      [`GET ${REF}/graph`]: { json: referenceGraph() },
      [`GET ${REF}/content`]: { body: new Blob(['x'], { type: 'image/png' }) },
      ...extra,
    })
  }

  it('opens the graph of a reference diagram and saves a correction as the next version', async () => {
    const mock = withGraph(true, {
      [`POST ${REF}/graph/edits`]: { json: referenceGraph({ version: 3 }) },
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Edit graph of coil.png' }))
    const dialog = await screen.findByRole('dialog', { name: 'coil.png' })
    expect(await within(dialog).findByRole('button', { name: 'Node Start' })).toBeVisible()
    expect(within(dialog).getByText('Read by the recognizer')).toBeVisible()
    expect(within(dialog).getByText('Version 2')).toBeVisible()

    await user.click(within(dialog).getByRole('button', { name: 'Node Add' }))
    const label = within(dialog).getByRole('textbox', { name: 'Node label' })
    await user.clear(label)
    await user.type(label, 'Sum')
    await user.click(within(dialog).getByRole('button', { name: 'Save label' }))
    await waitFor(() =>
      expect(mock.callsTo(`POST ${REF}/graph/edits`)[0]?.body).toEqual({
        expected_version: 2,
        edits: [{ op: 'relabel_node', id: 'n2', label: 'Sum' }],
        kind: null,
      }),
    )
    expect(await within(dialog).findByText('Version 3')).toBeVisible()
  })

  it('changes what the diagram is', async () => {
    const mock = withGraph(true, {
      [`POST ${REF}/graph/edits`]: { json: referenceGraph({ kind: 'tree', version: 3 }) },
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Edit graph of coil.png' }))
    const dialog = await screen.findByRole('dialog', { name: 'coil.png' })
    await user.selectOptions(
      await within(dialog).findByRole('combobox', { name: 'Diagram kind' }),
      'tree',
    )
    await waitFor(() =>
      expect(mock.callsTo(`POST ${REF}/graph/edits`)[0]?.body).toEqual({
        expected_version: 2,
        edits: [],
        kind: 'tree',
      }),
    )
  })

  it('queues the picture to be read again', async () => {
    const mock = withGraph(true, { [`POST ${REF}/recognize`]: { status: 202 } })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Edit graph of coil.png' }))
    const dialog = await screen.findByRole('dialog', { name: 'coil.png' })
    await user.click(await within(dialog).findByRole('button', { name: 'Read the picture again' }))
    expect(await screen.findByText(/being read again/)).toBeVisible()
    expect(mock.callsTo(`POST ${REF}/recognize`)).toHaveLength(1)
  })

  it('says when the graph cannot be loaded', async () => {
    withGraph(true, { [`GET ${REF}/graph`]: { status: 404, json: { detail: 'Not found.' } } })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Edit graph of coil.png' }))
    expect(await screen.findByText('The graph could not be loaded.')).toBeVisible()
  })
})

describe('permanent deletion (P28, D166)', () => {
  it('says why a question cannot be deleted yet', async () => {
    const mock = opened(detail(), {
      'GET /api/v1/questions/q-1/deletion': {
        json: {
          allowed: false,
          blockers: [
            "The paper 'Mid-term' links it: unlink it there, or delete the paper first.",
            'Unit U-0001 (open) uses it.',
          ],
        },
      },
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('dialog')
    const why = await within(dialog).findByRole('list', { name: 'Why not yet' })
    expect(why).toHaveTextContent('Unit U-0001 (open) uses it.')
    expect(within(dialog).queryByRole('button', { name: 'Delete permanently' })).toBeNull()
    expect(mock.callsTo('DELETE /api/v1/questions/q-1')).toHaveLength(0)
  })

  it('deletes a question no unfrozen unit uses, after asking', async () => {
    const mock = opened(detail(), {
      'GET /api/v1/questions/q-1/deletion': { json: { allowed: true, blockers: [] } },
      'DELETE /api/v1/questions/q-1': { status: 204 },
    })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/cannot be undone/)).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))
    await waitFor(() => expect(mock.callsTo('DELETE /api/v1/questions/q-1')).toHaveLength(1))
    expect(await screen.findByText('Question PHY-Q1 deleted.')).toBeVisible()
  })
})

describe('the Questions page (P35, D165)', () => {
  it('lists the SME’s questions with New question, and opens the editor from a card', async () => {
    opened()
    const user = userEvent.setup()
    renderApp('/sme/questions')
    expect(await screen.findByRole('heading', { name: 'Questions', level: 2 })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to Manage Question Papers' })).toHaveAttribute(
      'href',
      '/sme/papers',
    )
    await user.click(await screen.findByRole('button', { name: 'Open PHY-Q1' }))
    expect(await screen.findByRole('form', { name: 'Edit question' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to Questions' }))
    await user.click(await screen.findByRole('button', { name: 'New question' }))
    expect(await screen.findByRole('form', { name: 'New question' })).toBeVisible()
  })

  it('is for SMEs: the admin is sent to the admin page', async () => {
    opened(detail(), AS_ADMIN)
    renderApp('/sme/questions')
    expect(await screen.findByRole('heading', { name: 'Dashboard Overview' })).toBeInTheDocument()
  })

  it('does not follow a return address outside the portal', async () => {
    opened()
    renderApp('/sme/questions/q-1?return=https://example.test/steal')
    expect(await screen.findByRole('button', { name: 'Back to Questions' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Back to the question paper' })).toBeNull()
  })

  it('redirects the old /qna address', async () => {
    bank()
    renderApp('/qna')
    expect(await screen.findByRole('heading', { name: 'Questions', level: 2 })).toBeVisible()
  })
})

describe('the used-in warning (D223)', () => {
  const usage = {
    papers: [
      {
        id: 'p-1',
        title: 'Mid-term 2026',
        exam_code: 'QP-PHY-01',
        open_units: [{ id: 'u-1', code: 'U-0001', year: '2024' }],
      },
      { id: 'p-2', title: 'Re-test', exam_code: '', open_units: [] },
    ],
  }

  async function edit(extra: Parameters<typeof mockApi>[0]) {
    const mock = opened(detail(), {
      'PUT /api/v1/questions/q-1': { json: detail({ version: 2 }) },
      ...extra,
    })
    const user = await openFirst()
    const text = screen.getByRole('textbox', { name: 'Question Title / Text' })
    await user.type(text, ' Explain.')
    await user.click(screen.getByRole('button', { name: 'Save Question' }))
    return { mock, user }
  }

  it('lists the papers and open units, and saves only after confirming', async () => {
    const { mock, user } = await edit({ 'GET /api/v1/questions/q-1/usage': { json: usage } })
    const dialog = await screen.findByRole('dialog', { name: 'PHY-Q1 is used in 2 papers' })
    const papers = within(
      within(dialog).getByRole('list', { name: 'Papers that link this question' }),
    )
    expect(papers.getAllByRole('listitem')).toHaveLength(2)
    expect(papers.getByText('Mid-term 2026')).toBeVisible()
    expect(papers.getByText('QP-PHY-01')).toBeVisible()
    expect(papers.getByText('Open unit: U-0001 (2024)')).toBeVisible()
    expect(papers.getByText('No open unit uses it now.')).toBeVisible()
    expect(dialog).toHaveTextContent('scored again with the new version')
    expect(dialog).toHaveTextContent('Approved answers and frozen records keep')
    expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(0) // nothing saved yet
    await user.click(within(dialog).getByRole('button', { name: 'Save the question' }))
    await waitFor(() => expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(1))
    expect(await screen.findByText('Question saved.')).toBeVisible()
  })

  it('says so when the marks change under the slots that link it (O151)', async () => {
    const mock = opened(detail(), {
      'GET /api/v1/questions/q-1/usage': { json: usage },
      'PUT /api/v1/questions/q-1': { json: detail({ version: 2 }) },
    })
    const user = await openFirst()
    const marks = screen.getByRole('textbox', { name: 'Max marks' })
    await user.clear(marks)
    await user.type(marks, '5')
    await user.click(screen.getByRole('button', { name: 'Save Question' }))
    const dialog = await screen.findByRole('dialog', { name: 'PHY-Q1 is used in 2 papers' })
    expect(dialog).toHaveTextContent('You are changing the marks from 4 to 5.')
    expect(dialog).toHaveTextContent('carry 4')
    expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(0)
  })

  it('does not mention marks when they are unchanged', async () => {
    await edit({ 'GET /api/v1/questions/q-1/usage': { json: usage } })
    const dialog = await screen.findByRole('dialog', { name: 'PHY-Q1 is used in 2 papers' })
    expect(dialog).not.toHaveTextContent('changing the marks')
  })

  it('saves nothing when cancelled', async () => {
    const { mock, user } = await edit({ 'GET /api/v1/questions/q-1/usage': { json: usage } })
    const dialog = await screen.findByRole('dialog', { name: 'PHY-Q1 is used in 2 papers' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(0)
    expect(screen.getByRole('textbox', { name: 'Question Title / Text' })).toHaveValue(
      'State Lenz’s law. Explain.',
    ) // the edit is kept
  })

  it('does not ask for a question no paper links', async () => {
    const { mock } = await edit({})
    await waitFor(() => expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(1))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('asks again each time, and never saves a new question through the check', async () => {
    const mock = bank({
      'POST /api/v1/questions': { status: 201, json: detail({ id: 'q-7', code: 'PHY-Q7' }) },
      'GET /api/v1/questions/q-7': { json: detail({ id: 'q-7', code: 'PHY-Q7' }) },
    })
    const user = userEvent.setup()
    renderApp('/sme/questions/new')
    const form = await screen.findByRole('form', { name: 'New question' })
    await user.type(within(form).getByRole('textbox', { name: 'Question Title / Text' }), 'Why?')
    await user.selectOptions(
      await within(form).findByRole('combobox', { name: 'Subject' }),
      SUBJECT_ID,
    )
    await user.type(within(form).getByRole('textbox', { name: 'Max marks' }), '2')
    await user.type(within(form).getByRole('textbox', { name: 'Question code' }), 'PHY-Q7')
    await user.click(within(form).getByRole('button', { name: 'Save Question' }))
    await waitFor(() => expect(mock.callsTo('POST /api/v1/questions')).toHaveLength(1))
    // A new question is used nowhere: there is nothing to ask about.
    expect(mock.calls.some((c) => c.path.endsWith('/usage'))).toBe(false)
  })

  it('does not save when it cannot tell which papers use the question', async () => {
    const { mock } = await edit({
      'GET /api/v1/questions/q-1/usage': { status: 500, json: { detail: 'Boom.' } },
    })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not check which papers use this question.',
    )
    expect(mock.callsTo('PUT /api/v1/questions/q-1')).toHaveLength(0)
  })
})

describe('the similar-questions search beside the editor', () => {
  const lens = summary({
    id: 'q-2',
    code: 'PHY-Q2',
    text: 'Define the lens formula.',
    max_marks: 3,
  })

  function withSearch(extra: Parameters<typeof mockApi>[0] = {}) {
    return opened(detail(), {
      'GET /api/v1/questions': (call) => {
        const keyword = call.query.get('keyword') ?? ''
        const items = [summary(), lens].filter((q) =>
          q.text.toLowerCase().includes(keyword.toLowerCase()),
        )
        return { json: { items, total: items.length, limit: 12, offset: 0 } }
      },
      ...extra,
    })
  }

  it('waits for a few letters, then lists the other questions of my subjects', async () => {
    const mock = withSearch()
    const user = userEvent.setup()
    renderApp('/sme/questions/q-1')
    const panel = await screen.findByRole('complementary', { name: 'Similar questions' })
    expect(within(panel).getByText(/before writing it again/)).toBeVisible()
    await user.type(within(panel).getByRole('searchbox'), 'lens')
    const list = await within(panel).findByRole('list', { name: 'Matching questions' })
    expect(within(list).getByText('PHY-Q2')).toBeVisible()
    expect(within(list).getByText(/3 marks · Physics/)).toBeVisible()
    const search = mock
      .callsTo('GET /api/v1/questions')
      .filter((c) => c.query.has('keyword') && c.query.get('keyword') === 'lens')
    expect(search.length).toBeGreaterThan(0)
    expect(search[0]?.query.has('subject_id')).toBe(false) // all of my subjects
    // The question being edited is never offered to itself.
    await user.clear(within(panel).getByRole('searchbox'))
    await user.type(within(panel).getByRole('searchbox'), 'lenz')
    await within(panel).findByText('No other question matches.')
  })

  it('opens a match, carrying the way back to the paper', async () => {
    withSearch({ 'GET /api/v1/questions/q-2': { json: detail({ ...lens }) } })
    const user = userEvent.setup()
    renderApp('/sme/questions/q-1?return=/sme/papers/p-1')
    const panel = await screen.findByRole('complementary', { name: 'Similar questions' })
    await user.type(within(panel).getByRole('searchbox'), 'lens')
    const open = await within(panel).findByRole('link', { name: 'Open PHY-Q2' })
    expect(open).toHaveAttribute('href', '/sme/questions/q-2?return=%2Fsme%2Fpapers%2Fp-1')
  })

  it('clones a match as the start of a new question, without its code', async () => {
    const source = detail({ ...lens })
    withSearch({ 'GET /api/v1/questions/q-2': { json: source } })
    const user = userEvent.setup()
    renderApp('/sme/questions/new')
    const panel = await screen.findByRole('complementary', { name: 'Similar questions' })
    await user.type(within(panel).getByRole('searchbox'), 'lens')
    await user.click(await within(panel).findByRole('link', { name: 'Clone PHY-Q2 as a start' }))
    const form = await screen.findByRole('form', { name: 'New question' })
    await waitFor(() =>
      expect(within(form).getByRole('textbox', { name: 'Question Title / Text' })).toHaveValue(
        'Define the lens formula.',
      ),
    )
    expect(within(form).getByRole('textbox', { name: 'Question code' })).toHaveValue('')
    expect(within(form).getByRole('textbox', { name: 'Max marks' })).toHaveValue('3')
    expect(within(form).getByRole('textbox', { name: 'Reference answer (benchmark)' })).toHaveValue(
      'The induced emf opposes the change of flux.',
    )
    expect(within(form).getByRole('textbox', { name: 'Criterion 1 label' })).toHaveValue(
      'Names the effect',
    )
    // The diagram files are not copied: the form says so.
    expect(within(form).getByRole('note')).toHaveTextContent('Started from PHY-Q2; not copied')
  })
})
