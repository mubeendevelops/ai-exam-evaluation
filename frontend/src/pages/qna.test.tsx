import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { COLLEGE_ID, healthy, meOf, tokenOf } from '../test/fixtures'
import { mockApi, type Call } from '../test/mockApi'
import { detail, OTHER_COLLEGE, SUBJECT_ID, summary, type Detail } from '../test/qnaFixtures'
import { renderApp } from '../test/render'

beforeEach(() => globalThis.sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const physics = {
  id: SUBJECT_ID,
  code: 'PHY',
  name: 'Physics',
  owning_college_id: COLLEGE_ID,
  owned: true,
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

function opened(question: Detail = detail(), extra: Parameters<typeof mockApi>[0] = {}) {
  return bank({ [`GET /api/v1/questions/${question.id}`]: { json: question }, ...extra }, [
    summary({ id: question.id }),
  ])
}

async function openFirst() {
  const user = userEvent.setup()
  renderApp('/qna')
  await user.click(await screen.findByRole('button', { name: 'Open PHY-Q1' }))
  await screen.findByRole('region', { name: 'Rubric' })
  return user
}

describe('repository browser', () => {
  it('shows question cards with code, difficulty, marks, subject, keys and owner', async () => {
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
        owned: false,
        owner_name: 'Other College',
        owning_college_id: OTHER_COLLEGE,
      }),
    ])
    renderApp('/qna')
    const first = await screen.findByRole('article', { name: 'Question PHY-Q1' })
    expect(within(first).getByText('Easy')).toBeVisible()
    expect(within(first).getByText('4 Marks')).toBeVisible()
    expect(within(first).getByText('Physics')).toBeVisible()
    expect(within(first).getByText('Electromagnetism')).toBeVisible()
    expect(within(first).getByText('2 reference keys')).toBeVisible()
    expect(within(first).getByText('Your college')).toBeVisible()

    const second = screen.getByRole('article', { name: 'Question MAT-Q1' })
    expect(within(second).getByText('Hard')).toBeVisible()
    expect(within(second).getByText('1 Mark')).toBeVisible()
    expect(within(second).getByText('0 reference keys')).toBeVisible()
    expect(within(second).getByText('Owned by Other College')).toBeVisible()
    expect(screen.getByText(/Showing/)).toHaveTextContent('Showing 2 of 2 Questions')
  })

  it('sends every filter to the server', async () => {
    const mock = bank()
    const user = userEvent.setup()
    renderApp('/qna')
    await screen.findByRole('article', { name: 'Question PHY-Q1' })
    const last = (): Call['query'] => mock.callsTo('GET /api/v1/questions').at(-1)!.query

    await user.selectOptions(screen.getByRole('combobox', { name: /Subject/ }), SUBJECT_ID)
    await waitFor(() => expect(last().get('subject_id')).toBe(SUBJECT_ID))
    await user.selectOptions(screen.getByRole('combobox', { name: /Topic/ }), 'AC Circuits')
    await waitFor(() => expect(last().get('topic')).toBe('AC Circuits'))
    await user.selectOptions(screen.getByRole('combobox', { name: /Difficulty/ }), 'hard')
    await waitFor(() => expect(last().get('difficulty')).toBe('hard'))
    await user.click(screen.getByRole('checkbox', { name: 'My college only' }))
    await waitFor(() => expect(last().get('mine')).toBe('true'))
    await user.type(screen.getByRole('searchbox', { name: 'Search questions' }), 'lenz')
    await waitFor(() => expect(last().get('keyword')).toBe('lenz'))
    await user.type(screen.getByRole('searchbox', { name: 'Code' }), 'phy-q')
    await waitFor(() => expect(last().get('code')).toBe('phy-q'))
    expect(Object.fromEntries(last())).toMatchObject({
      subject_id: SUBJECT_ID,
      topic: 'AC Circuits',
      difficulty: 'hard',
      mine: 'true',
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
    renderApp('/qna')
    expect(await screen.findByText('The question bank is empty')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Add the first question' }))
    expect(screen.getByRole('dialog', { name: 'Add New Question' })).toBeVisible()
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
    renderApp('/qna')
    await screen.findByRole('article', { name: 'Question Q-0' })
    expect(screen.getByText('Page 1 of 3')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('article', { name: 'Question Q-12' })
    expect(mock.callsTo('GET /api/v1/questions').at(-1)?.query.get('offset')).toBe('12')
  })
})

describe('question detail', () => {
  it('shows the benchmark key, rubric, glossary and files, and the evaluator link', async () => {
    opened()
    await openFirst()
    expect(screen.getByRole('heading', { name: 'State Lenz’s law.' })).toBeVisible()
    const keys = screen.getByRole('region', { name: 'Benchmark answers' })
    expect(within(keys).getByText(/induced emf opposes the change of flux/)).toBeVisible()
    const rubric = screen.getByRole('region', { name: 'Rubric' })
    expect(within(rubric).getByText('Names the effect')).toBeVisible()
    expect(within(rubric).getByText('1 items, 1 required')).toBeVisible()
    expect(within(rubric).getByText('Weights add up to 4 of 4 marks.')).toBeVisible()
    const glossary = screen.getByRole('region', { name: 'Glossary' })
    expect(within(glossary).getByText('induced emf')).toBeVisible()
    expect(within(glossary).getByText('coil')).toBeVisible()
    const files = screen.getByRole('region', { name: 'Attached files' })
    expect(within(files).getByText('key.pdf')).toBeVisible()
    expect(within(files).getByText('2 KB')).toBeVisible()
    expect(within(files).getByText('lenz')).toBeVisible()
    expect(within(files).getByText('0 nodes · 0 edges')).toBeVisible()
    expect(screen.getByRole('link', { name: /Test in AI Evaluator/ })).toHaveAttribute(
      'href',
      '/evaluate',
    )
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

  it('offers Edit and the upload actions to the owner, not Copy', async () => {
    opened()
    await openFirst()
    expect(screen.getByRole('button', { name: 'Edit question' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Upload Answer Key' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Edit rubric' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Upload reference diagram' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Copy to my college' })).toBeNull()
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('offers only Copy for another college’s question, naming the owner', async () => {
    opened(detail({ owned: false, owner_name: 'Other College', owning_college_id: OTHER_COLLEGE }))
    await openFirst()
    expect(screen.getByRole('note')).toHaveTextContent('Owned by Other College')
    expect(screen.getByRole('button', { name: 'Copy to my college' })).toBeVisible()
    for (const name of [
      'Edit question',
      'Upload Answer Key',
      'Edit rubric',
      'Edit glossary',
      'Add reference answer',
      'Remove answer',
      'Upload reference diagram',
    ]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    // reading stays possible: the files can be downloaded
    expect(screen.getByRole('button', { name: 'Download key.pdf' })).toBeVisible()
  })

  it('copies to my college and opens the copy', async () => {
    const copy = detail({ id: 'q-9', code: 'PHY-Q1-2', owned: true })
    const mock = opened(
      detail({ owned: false, owner_name: 'Other College', owning_college_id: OTHER_COLLEGE }),
      {
        'POST /api/v1/questions/q-1/copy': { status: 201, json: copy },
        'GET /api/v1/questions/q-9': { json: copy },
      },
    )
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Copy to my college' }))
    expect(await screen.findByText('Copied to your college as PHY-Q1-2.')).toBeVisible()
    expect(mock.callsTo('POST /api/v1/questions/q-1/copy')).toHaveLength(1)
    expect(await screen.findByRole('button', { name: 'Edit question' })).toBeVisible()
    expect(screen.getByText('PHY-Q1-2')).toBeVisible()
  })

  it('flags weights that do not add up', async () => {
    opened(detail({ rubric: { ...detail().rubric, total: 3, complete: false } }))
    await openFirst()
    expect(screen.getByText(/Weights add up to 3 of 4 marks: the AI cannot score/)).toBeVisible()
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
    renderApp('/qna?question=nope')
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

describe('New Question modal and the rubric editor', () => {
  async function fillQuestion(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: 'New Question' }))
    const dialog = screen.getByRole('dialog', { name: 'Add New Question' })
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
    renderApp('/qna')
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
    // after saving, the new question is open
    expect(await screen.findByText('Question saved.')).toBeVisible()
    expect(screen.getByRole('region', { name: 'Rubric' })).toBeVisible()
  })

  it('shows the weight total live and shares the marks evenly', async () => {
    bank()
    const user = userEvent.setup()
    renderApp('/qna')
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
    renderApp('/qna')
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
    expect(screen.getByRole('dialog', { name: 'Add New Question' })).toBeVisible() // kept open
  })

  it('edits a question and its rubric together, keeping criterion ids', async () => {
    const mock = opened(detail(), { 'PUT /api/v1/questions/q-1': { json: detail({ version: 2 }) } })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Edit question' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit PHY-Q1' })
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

  it('edits just the rubric from the detail page, with diagrams to choose from', async () => {
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
    const mock = opened(q, { 'PUT /api/v1/questions/q-1/rubric': { json: q.rubric } })
    const user = await openFirst()
    await user.click(screen.getByRole('button', { name: 'Edit rubric' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit rubric' })
    const diagram = within(dialog).getByRole('combobox', { name: 'Criterion 1 reference diagram' })
    expect(diagram).toHaveValue('d-1')
    expect(within(diagram).getByRole('option', { name: 'coil.png' })).toBeInTheDocument()
    await user.selectOptions(
      within(dialog).getByRole('combobox', { name: 'Criterion 1 compared part' }),
      'edges',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Save rubric' }))
    await waitFor(() =>
      expect(mock.callsTo('PUT /api/v1/questions/q-1/rubric')[0]?.body).toEqual({
        criteria: [
          {
            id: 'c-9',
            type: 'diagram',
            label: 'Nodes',
            weight: 4,
            params: { reference_diagram_id: 'd-1', component: 'edges' },
          },
        ],
      }),
    )
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
