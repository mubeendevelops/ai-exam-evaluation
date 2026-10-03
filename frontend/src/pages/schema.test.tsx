import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { components } from '../api/schema'
import type { BlueprintDocument } from '../lib/blueprint'
import { COLLEGE_ID, healthy, meOf, tokenOf } from '../test/fixtures'
import { mockApi, type Call } from '../test/mockApi'
import { renderApp } from '../test/render'

type Check = components['schemas']['ValidationOut']
type Subject = components['schemas']['SubjectOut']

const SUBJECT_ID = '33333333-3333-4333-8333-333333333333'
const physics: Subject = {
  id: SUBJECT_ID,
  code: 'PHY-501',
  name: 'Physics',
  owning_college_id: COLLEGE_ID,
  owned: true,
}

beforeEach(() => globalThis.sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

/** A stand-in for the server's verdict: only the title decides (the rules are tested in Python). */
function verdict(call: Call): Check {
  const body = call.body as BlueprintDocument
  const issues = body.title.trim() === '' ? [{ path: 'title', message: 'must not be empty' }] : []
  return {
    valid: issues.length === 0,
    issues,
    warnings: [],
    sections: body.sections.map((s) => ({
      label: s.label,
      method: s.method,
      items: s.items.length,
      counted: s.choice.rule === 'any' ? Number(s.choice.n) : s.items.length,
      max_marks: 0,
    })),
    computed_total: Number(body.total_marks),
    question_count: body.sections.reduce((n, s) => n + s.items.length, 0),
    unlinked: ['1'],
  }
}

function designer(extra: Parameters<typeof mockApi>[0] = {}, subjects: Subject[] = [physics]) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf() },
    'GET /api/v1/auth/me': { json: meOf() },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/subjects': () => ({ json: subjects }),
    'POST /api/v1/blueprints/validate': (call) => ({ json: verdict(call) }),
    ...extra,
  })
}

function shownJson(): BlueprintDocument {
  return JSON.parse(screen.getByTestId('schema-json').textContent ?? '{}') as BlueprintDocument
}

async function open() {
  const user = userEvent.setup()
  renderApp('/schema')
  await screen.findByRole('heading', { name: 'Generated Question Schema (JSON)' })
  return user
}

async function type(user: ReturnType<typeof userEvent.setup>, name: string | RegExp, text: string) {
  const box = screen.getByRole('textbox', { name })
  await user.clear(box)
  await user.type(box, text)
}

describe('schema designer layout', () => {
  it('has the form on the left and the generated JSON panel on the right', async () => {
    designer()
    await open()
    expect(
      screen.getByRole('heading', { name: 'Question Paper Studio & Schema Engine' }),
    ).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Exam Blueprint' })).toBeVisible()
    expect(screen.getByRole('heading', { name: /Section Breakdown\s+Architecture/ })).toBeVisible()
    for (const name of ['Copy JSON', 'Download .json', 'Reset Form', 'Add Section']) {
      expect(screen.getByRole('button', { name })).toBeVisible()
    }
    const doc = shownJson()
    expect(doc.schema_version).toBe('1.0') // the real schema, not the prototype's hard-coded JSON
    expect(Object.keys(doc)).toContain('negative_marking')
    expect(doc.sections).toHaveLength(1)
  })
})

describe('live JSON panel', () => {
  it('rebuilds the JSON from every input', async () => {
    designer()
    const user = await open()

    await type(user, 'Exam Title / Header', 'Physics Mid-Term')
    await type(user, 'Course Code', 'PHY-501')
    await type(user, 'Duration (Minutes)', '90')
    expect(shownJson()).toMatchObject({
      title: 'Physics Mid-Term',
      course_code: 'PHY-501',
      duration_minutes: 90,
    })

    await user.selectOptions(await screen.findByRole('combobox', { name: 'Subject' }), SUBJECT_ID)
    await user.selectOptions(screen.getByRole('combobox', { name: /Negative marking/ }), '0.25')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Marks are rounded to' }), '1')
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Section 1 evaluation method' }),
      'omr_bubble_scan',
    )
    await type(user, 'Section 1 title', 'Multiple choice')
    expect(shownJson()).toMatchObject({
      subject_id: SUBJECT_ID,
      negative_marking: 0.25,
      mark_step: 1,
    })
    expect(shownJson().sections[0]).toMatchObject({
      title: 'Multiple choice',
      method: 'omr_bubble_scan',
      choice: { rule: 'all' },
    })

    // Marks of one question change the total that follows the sections.
    await type(user, 'Section 1 item 1 marks', '4')
    expect(shownJson().total_marks).toBe(12) // 4 + 4x2
    expect(screen.getByText('Total Paper Marks').parentElement).toHaveTextContent('12 Marks')

    // A typed total is kept, and "Auto" gives the calculated one back.
    await type(user, 'Total Marks', '15')
    expect(shownJson().total_marks).toBe(15)
    await user.click(screen.getByRole('button', { name: 'Auto' }))
    expect(shownJson().total_marks).toBe(12)
  })

  it('builds QP-CI: 5 of 7 x 2, 4 of 7 x 5, 2 of 3 x 10 = 50', async () => {
    designer()
    const user = await open()

    async function section(n: number, count: string, marks: string, any: string) {
      const name = `Section ${n}`
      await type(user, `${name} question count`, count)
      await type(user, `${name} marks per question`, marks)
      await user.click(screen.getByRole('button', { name: `Fill ${name.toLowerCase()}` }))
      await user.selectOptions(screen.getByRole('combobox', { name: `${name} choice rule` }), 'any')
      await type(user, `${name} N`, any)
    }

    await section(1, '7', '2', '5')
    await user.click(screen.getByRole('button', { name: 'Add Section' }))
    await section(2, '7', '5', '4')
    await user.click(screen.getByRole('button', { name: 'Add Section' }))
    await section(3, '3', '10', '2')

    const doc = shownJson()
    expect(doc.total_marks).toBe(50)
    expect(doc.sections.map((s) => [s.label, s.items.length, s.choice])).toEqual([
      ['A', 7, { rule: 'any', n: 5 }],
      ['B', 7, { rule: 'any', n: 4 }],
      ['C', 3, { rule: 'any', n: 2 }],
    ])
    const labels = doc.sections.flatMap((s) =>
      s.items.map((i) => (i.type === 'question' ? i.label : '')),
    )
    expect(labels).toEqual(Array.from({ length: 17 }, (_, i) => String(i + 1)))
    expect(screen.getByText('Calculated Question Count').parentElement).toHaveTextContent(
      '17 Questions',
    )
    expect(screen.getByText('Total Paper Marks').parentElement).toHaveTextContent('50 Marks')
    expect(
      within(screen.getByRole('region', { name: 'Section 2' })).getByText(/Best 4 of 7 count/),
    ).toBeVisible()
  })

  it('builds QP-IPR section C: Q12 or Q13, each (a) 10 + (b) 5', async () => {
    designer()
    const user = await open()
    await type(user, 'Section 1 question count', '1')
    await type(user, 'Section 1 marks per question', '15')
    await user.click(screen.getByRole('button', { name: 'Fill section 1' }))
    await user.click(screen.getByRole('button', { name: 'Add OR pair to section 1' }))
    // The plain question 1 stays; the pair is item 2 with questions 2 and 3.
    for (const alt of [1, 2]) {
      const name = `Section 1 item 2 alternative ${alt}`
      await type(user, `${name} marks`, '15')
      await user.click(screen.getByRole('button', { name: `Show details of ${name}` }))
      await user.click(screen.getByRole('button', { name: 'Add sub-part' }))
      await user.click(screen.getByRole('button', { name: 'Add sub-part' }))
      await type(user, `${name} part 1 marks`, '10')
      await type(user, `${name} part 2 marks`, '5')
      expect(screen.getByText('Sub-parts add up to 15 of 15 marks')).toBeVisible()
      await user.click(screen.getByRole('button', { name: `Hide details of ${name}` }))
    }
    const items = shownJson().sections[0]!.items
    const pair = items[1]!
    expect(pair.type).toBe('or')
    if (pair.type !== 'or') return
    expect(pair.alternatives.map((a) => a.label)).toEqual(['2', '3'])
    expect(pair.alternatives.map((a) => a.parts?.map((p) => [p.label, p.marks]))).toEqual([
      [
        ['a', 10],
        ['b', 5],
      ],
      [
        ['a', 10],
        ['b', 5],
      ],
    ])
    expect(shownJson().total_marks).toBe(30) // question 1 (15) + one of the pair (15)
  })

  it('writes step marks and a question link into the JSON', async () => {
    designer()
    const user = await open()
    const name = 'Section 1 item 1'
    await type(user, `${name} marks`, '5')
    await user.click(screen.getByRole('button', { name: `Show details of ${name}` }))
    await user.click(screen.getByRole('button', { name: 'Add step' }))
    await user.click(screen.getByRole('button', { name: 'Add step' }))
    await type(user, `${name} step 1 marks`, '2')
    await type(user, `${name} step 2 marks`, '3')
    await type(user, `${name} question ID`, '44444444-4444-4444-8444-444444444444')
    const first = shownJson().sections[0]!.items[0]!
    expect(first).toMatchObject({
      question_id: '44444444-4444-4444-8444-444444444444',
      steps: [
        { label: 'Step 1', marks: 2 },
        { label: 'Step 2', marks: 3 },
      ],
    })
    expect(screen.getByText('Steps add up to 5 of 5 marks')).toBeVisible()
  })

  it('adds and removes sections and questions', async () => {
    designer()
    const user = await open()
    await user.click(screen.getByRole('button', { name: 'Add Section' }))
    expect(shownJson().sections.map((s) => s.label)).toEqual(['A', 'B'])
    await user.click(screen.getByRole('button', { name: 'Remove Section 1' }))
    expect(shownJson().sections.map((s) => s.label)).toEqual(['B'])
    await user.click(screen.getByRole('button', { name: 'Remove Section 1 item 1' }))
    expect(shownJson().sections[0]!.items).toHaveLength(4)
    await user.click(screen.getByRole('button', { name: 'Add question to section 1' }))
    expect(shownJson().sections[0]!.items).toHaveLength(5)
    await user.click(screen.getByRole('button', { name: 'Remove Section 1' }))
    expect(screen.getByText(/A blueprint needs at least one section/)).toBeVisible()
  })
})

describe('valid / invalid status', () => {
  it("shows the server's verdict and its problems for exactly what is on screen", async () => {
    const mock = designer()
    const user = await open()
    const status = screen.getByRole('status', { name: 'Schema status' })
    expect(status).toHaveTextContent('Checking…')

    await waitFor(() => expect(status).toHaveTextContent('Invalid · 1 problem'))
    const problems = screen.getByRole('list', { name: 'Problems' })
    expect(within(problems).getByText('must not be empty')).toBeVisible()
    expect(within(problems).getByText('title')).toBeVisible()

    await type(user, 'Exam Title / Header', 'Physics Mid-Term')
    await waitFor(() => expect(status).toHaveTextContent('Valid'))
    expect(screen.queryByRole('list', { name: 'Problems' })).toBeNull()
    expect(screen.getByText(/not linked to a question yet/)).toBeVisible()

    // The request is the JSON on screen.
    const last = mock.callsTo('POST /api/v1/blueprints/validate').at(-1)
    expect(last?.body).toEqual(shownJson())
  })

  it('says so when the server cannot be reached', async () => {
    designer({ 'POST /api/v1/blueprints/validate': { status: 500, json: { detail: 'down' } } })
    await open()
    expect(await screen.findByText('Cannot check right now')).toBeVisible()
    expect(screen.getByTestId('schema-json')).toHaveTextContent('"schema_version": "1.0"')
  })
})

describe('copy, download, reset', () => {
  it('copies the JSON', async () => {
    designer()
    const user = await open()
    await type(user, 'Exam Title / Header', 'Copy me')
    await user.click(screen.getByRole('button', { name: 'Copy JSON' }))
    expect(await screen.findByText('JSON copied to the clipboard.')).toBeVisible()
    expect(await navigator.clipboard.readText()).toBe(screen.getByTestId('schema-json').textContent)
  })

  it('downloads the JSON as <course code>.blueprint.json', async () => {
    designer()
    const blobs: Blob[] = []
    vi.stubGlobal(
      'URL',
      Object.assign(URL, {
        createObjectURL: vi.fn((b: Blob) => (blobs.push(b), 'blob:schema')),
        revokeObjectURL: vi.fn(),
      }),
    )
    let name = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      name = this.download
    })
    const user = await open()
    await type(user, 'Course Code', 'PHY-501')
    await user.click(screen.getByRole('button', { name: 'Download .json' }))
    expect(name).toBe('phy-501.blueprint.json')
    expect(blobs).toHaveLength(1)
    expect(blobs[0]!.type).toBe('application/json')
    expect(JSON.parse(await blobs[0]!.text())).toEqual(shownJson())
    vi.restoreAllMocks()
  })

  it('asks before resetting, then restores the starting form', async () => {
    designer()
    const user = await open()
    await type(user, 'Exam Title / Header', 'Keep me')
    await user.click(screen.getByRole('button', { name: 'Add Section' }))

    await user.click(screen.getByRole('button', { name: 'Reset Form' }))
    const dialog = screen.getByRole('dialog', { name: 'Reset the form?' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(shownJson().title).toBe('Keep me')

    await user.click(screen.getByRole('button', { name: 'Reset Form' }))
    await user.click(screen.getByRole('button', { name: 'Yes, reset' }))
    expect(shownJson().title).toBe('')
    expect(shownJson().sections).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue('')
  })

  it('keeps the form when you leave the tab and come back', async () => {
    designer()
    const user = await open()
    await type(user, 'Exam Title / Header', 'Still here')
    await user.click(screen.getByRole('button', { name: 'Add Section' }))
    cleanup() // leave the page

    renderApp('/schema')
    expect(await screen.findByRole('textbox', { name: 'Exam Title / Header' })).toHaveValue(
      'Still here',
    )
    expect(shownJson().sections.map((s) => s.label)).toEqual(['A', 'B'])
  })
})

describe('subjects', () => {
  it('lists the global subjects and creates a new one for my college', async () => {
    const subjects: Subject[] = [physics]
    const mock = designer(
      {
        'POST /api/v1/subjects': (call) => {
          const body = call.body as { code: string; name: string }
          const made: Subject = {
            id: '55555555-5555-4555-8555-555555555555',
            ...body,
            owning_college_id: COLLEGE_ID,
            owned: true,
          }
          subjects.push(made)
          return { status: 201, json: made }
        },
      },
      subjects,
    )
    const user = await open()
    const select = await screen.findByRole('combobox', { name: 'Subject' })
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Select a subject…', 'Physics (PHY-501)'])

    await user.click(screen.getByRole('button', { name: 'New' }))
    await user.type(screen.getByRole('textbox', { name: 'Subject code' }), 'MAT-101')
    await user.type(screen.getByRole('textbox', { name: 'Subject name' }), 'Mathematics')
    await user.click(screen.getByRole('button', { name: 'Create subject' }))

    await waitFor(() => expect(shownJson().subject_id).toBe('55555555-5555-4555-8555-555555555555'))
    expect(mock.callsTo('POST /api/v1/subjects')[0]?.body).toEqual({
      code: 'MAT-101',
      name: 'Mathematics',
    })
    expect(await screen.findByRole('option', { name: 'Mathematics (MAT-101)' })).toBeInTheDocument()
  })

  it('shows the server reason when a subject is refused', async () => {
    designer({
      'POST /api/v1/subjects': { status: 422, json: { detail: 'subject code must not be blank' } },
    })
    const user = await open()
    await user.click(await screen.findByRole('button', { name: 'New' }))
    await user.type(screen.getByRole('textbox', { name: 'Subject code' }), 'X')
    await user.type(screen.getByRole('textbox', { name: 'Subject name' }), 'Y')
    await user.click(screen.getByRole('button', { name: 'Create subject' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('subject code must not be blank')
  })
})
