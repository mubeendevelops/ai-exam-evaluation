import { expect, test, type Page } from '@playwright/test'
import { FakeApi } from './fakeApi'
import { FakeBooklets } from './fakeBooklets'

async function signIn(page: Page) {
  await new FakeApi().install(page)
  const booklets = new FakeBooklets()
  await booklets.install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page.getByRole('heading', { name: 'Questions & Answers Repository' })).toBeVisible()
  return booklets
}

test('upload a booklet, watch it being read, see its segments, correct a line and see the re-score', async ({
  page,
}) => {
  const booklets = await signIn(page)
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'AI Evaluation' })
    .click()
  await expect(page.getByRole('list', { name: 'Evaluation steps' })).toContainText('1. Scan Upload')

  // --- step 1: student, exam, drop the file ---
  const drop = page.getByRole('button', { name: /Drag and drop scanned student answer sheets/ })
  await expect(drop).toHaveAttribute('aria-disabled', 'true')
  await page.getByRole('combobox', { name: 'Student' }).click()
  await page.getByRole('option', { name: /Test Student One/ }).click()
  await page.getByLabel('Exam').selectOption({ label: 'Mid-term Physics' })
  await expect(drop).toHaveAttribute('aria-disabled', 'false')
  await page.getByLabel('Answer sheet files').setInputFiles({
    name: 'booklet.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.7 synthetic'),
  })
  await expect(page.getByText('Test Student One: file uploaded')).toBeVisible()
  expect(booklets.uploadRequests).toBe(1)
  const card = page.getByRole('article', { name: 'Submission Test Student One' })
  await expect(card).toContainText('USN: TST001')

  // --- step 2: the machine is still working: the beam sweeps the page ---
  await card.getByRole('button', { name: 'Run AI Eval' }).click()
  await expect(page).toHaveURL(/\/evaluate\?booklet=/)
  await expect(
    page
      .getByRole('list', { name: 'Evaluation steps' })
      .getByRole('button', { name: '2. AI Segmentation' }),
  ).toHaveAttribute('aria-current', 'step')
  await expect(page.getByTestId('scan-beam')).toBeVisible()

  // --- the segments appear when the booklet is scored; the screen takes the lock ---
  const one = page.getByRole('article', { name: /Question 1 Answer Clip/ })
  await expect(one).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/You have this booklet open/)).toBeVisible()
  await expect(page.getByTestId('scan-beam')).toBeHidden()
  await expect(page.getByRole('button', { name: 'Answer 1 on page 1' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Answer 2 on page 1' })).toBeVisible()
  await expect(one).toContainText('OCR confidence 65%')
  await expect(one).toContainText('Suggested 1 / 3')
  await expect(page.getByRole('region', { name: 'Unassigned tray' })).toContainText(
    'Every piece of text is assigned',
  )

  // --- correct the low-confidence line ---
  await one.getByRole('button', { name: 'Line 2, low OCR confidence: the emf is induced' }).click()
  const box = one.getByRole('textbox', { name: 'Correct the text of this line' })
  await box.fill('the emf is induced in the coil')
  await one.getByRole('button', { name: 'Save text' }).click()
  await expect(page.getByText(/Line saved and kept for the OCR benchmark/)).toBeVisible()
  expect(booklets.edits).toEqual([{ expected_version: 5, text: 'the emf is induced in the coil' }])

  // --- the re-score arrives ---
  const results = page.getByRole('region', { name: 'Re-score results' })
  await expect(results).toContainText('Re-scoring…')
  await expect(page.getByRole('button', { name: 'Proceed to Evaluation' })).toBeDisabled()
  await expect(results).toContainText('new suggestion 2.5 / 3', { timeout: 10_000 })
  await expect(results).toContainText('(was 1)')
  await expect(one).toContainText('Suggested 2.5 / 3')
  await expect(
    one.getByRole('button', { name: /Line 2: the emf is induced in the coil/ }),
  ).toBeVisible()

  // --- on to the evaluation view ---
  await page.getByRole('button', { name: 'Proceed to Evaluation' }).click()
  await expect(page).toHaveURL(/step=3/)
  await expect(page.getByRole('region', { name: 'Panel A: Answer Mapping' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Panel B: AI Diagnostic Reasoning' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Panel C: Teacher Final Grading' })).toBeVisible()
  await expect(page.getByText('Suggested AI Marks: 2.5 / 3')).toBeVisible()
})

test('approve every answer, approve the booklet, reopen one, amend it and find result sheet v2', async ({
  page,
}) => {
  const booklets = await signIn(page)
  booklets.startScored()
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'AI Evaluation' })
    .click()
  await page
    .getByRole('article', { name: 'Submission Test Student One' })
    .getByRole('button', { name: 'Open' })
    .click()
  await page.getByRole('button', { name: 'Proceed to Evaluation' }).click()
  await expect(page).toHaveURL(/step=3/)
  await expect(
    page
      .getByRole('list', { name: 'Evaluation steps' })
      .getByRole('button', { name: '3. Evaluation View' }),
  ).toHaveAttribute('aria-current', 'step')
  await expect(page.getByText(/Evaluating Student:/)).toContainText(
    'Test Student One (USN: TST001)',
  )
  await expect(page.getByText(/You have this booklet open\./)).toBeVisible()

  // --- question 1: the AI's mark is accepted as it is ---
  const mapping = page.getByRole('region', { name: 'Panel A: Answer Mapping' })
  await expect(mapping).toContainText('State Lenz’s law.')
  await expect(mapping.getByRole('region', { name: "Student's answer" })).toContainText(
    'Lenz law opposes the change',
  )
  const grading = page.getByRole('region', { name: 'Panel C: Teacher Final Grading' })
  await grading.getByRole('button', { name: 'Approve answer' }).click()
  await expect(page.getByText('Question 1 approved.')).toBeVisible()

  // --- question 2: the teacher overrides the mark, with a tag and a remark ---
  await expect(mapping).toContainText('Define resonance in an LCR circuit.')
  await expect(page.getByText('1 of 2 answers approved')).toBeVisible()
  await grading.getByRole('switch', { name: 'Override AI Score' }).click()
  const marks = grading.getByLabel(/Teacher marks/)
  await marks.fill('2.3')
  await expect(grading.getByRole('alert')).toContainText('steps of 0.5')
  await expect(grading.getByRole('button', { name: 'Approve answer' })).toBeDisabled()
  await marks.fill('2')
  await grading.getByRole('button', { name: 'Well explained' }).click()
  await grading.getByLabel('Remarks for the student').fill('Add the formula.')
  await grading.getByRole('button', { name: 'Approve answer' }).click()

  // --- the last approval leads to the summary; the booklet can be approved ---
  await expect(page.getByRole('region', { name: 'Booklet summary' })).toBeVisible()
  const table = page.getByRole('table')
  await expect(table).toContainText('1 / 3')
  await expect(table).toContainText('2 / 3')
  await expect(table).toContainText('overridden')
  await expect(page.getByText('3 / 6', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Approve booklet' }).click()
  await expect(page.getByText(/Booklet approved\. Result sheet v1 is stored/)).toBeVisible()
  const sheets = page.getByRole('region', { name: 'Result sheets' })
  await expect(sheets).toContainText('Result sheet v1')
  await expect(sheets).not.toContainText('Result sheet v2')
  await expect(page.getByRole('button', { name: 'Approve booklet' })).toHaveCount(0)

  // --- reopen question 2: an amendment draft; v1 stays valid ---
  await page.getByRole('button', { name: 'Reopen to amend question 2' }).click()
  await page.getByLabel('Reason (optional)').fill('Formula was on the next page.')
  await page.getByRole('button', { name: 'Open amendment' }).click()
  await expect(page.getByText('Amendment draft', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Draft amendment')).toBeVisible()
  await expect(page.getByText('Reason: Formula was on the next page.')).toBeVisible()

  // --- amend it: approving the last draft issues v2 ---
  await grading.getByRole('switch', { name: 'Override AI Score' }).click()
  await grading.getByLabel(/Teacher marks/).fill('3')
  await grading.getByRole('button', { name: 'Approve amendment' }).click()
  await expect(page.getByText(/The amendment is complete/)).toBeVisible()
  await expect(sheets).toContainText('Result sheet v2')
  await expect(sheets).toContainText('Result sheet v1')
  await expect(sheets.getByText('Current').locator('..')).toContainText('v2')
  await expect(
    page.getByRole('region', { name: 'Booklet summary' }).getByText('4 / 6', { exact: true }),
  ).toBeVisible()

  expect(booklets.decided.map((d) => d.call)).toEqual([
    'approve 1',
    'approve 2',
    'reopen 2',
    'approve 2',
  ])
  expect(booklets.decided[0]?.body).toMatchObject({ teacher_mark: null, tags: [], remarks: '' })
  expect(booklets.decided[1]?.body).toMatchObject({
    teacher_mark: 2,
    tags: ['Well explained'],
    remarks: 'Add the formula.',
  })
  expect(booklets.decided[2]?.body).toMatchObject({ reason: 'Formula was on the next page.' })
  expect(booklets.decided[3]?.body).toMatchObject({ teacher_mark: 3 })
})

test('five booklets fill the queue: the sixth must wait until the machine takes one', async ({
  page,
}) => {
  await signIn(page)
  // The booklet list and uploads, for five queued booklets (the rest stays on FakeBooklets).
  const queued: Record<string, unknown>[] = []
  await page.route(
    (url) => url.pathname === '/api/v1/booklets',
    async (route) => {
      const method = route.request().method()
      const list = () => ({
        items: queued,
        total: queued.length,
        limit: 100,
        offset: 0,
        waiting: queued.length,
        max_waiting: 5,
      })
      if (method === 'POST') {
        if (queued.length >= 5)
          return route.fulfill({
            status: 429,
            json: { detail: 'You already have 5 booklets waiting.' },
          })
        queued.unshift({
          id: `55555555-5555-4555-8555-00000000000${queued.length}`,
          status: 'uploaded',
          student: {
            id: '44444444-4444-4444-8444-444444444444',
            name: 'Test Student One',
            usn: 'TST001',
          },
          blueprint: {
            id: '66666666-6666-4666-8666-666666666666',
            version: 1,
            title: 'Mid-term Physics',
          },
          uploaded_by: '22222222-2222-4222-8222-222222222222',
          uploaded_at: `2026-10-06T08:0${queued.length}:00Z`,
          version: 1,
          page_count: 0,
          pages_cleaned: 0,
          flagged_pages: [],
          pages_read: 0,
          needs_text_pages: [],
          failure_reason: null,
          duplicate_of: [],
          result: null,
        })
        return route.fulfill({ status: 201, json: queued[0] })
      }
      return route.fulfill({ status: 200, json: list() })
    },
  )
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'AI Evaluation' })
    .click()
  await page.getByRole('combobox', { name: 'Student' }).click()
  await page.getByRole('option', { name: /Test Student One/ }).click()
  await page.getByLabel('Exam').selectOption({ label: 'Mid-term Physics' })

  const drop = page.getByRole('button', { name: /Drag and drop scanned student answer sheets/ })
  for (let n = 1; n <= 5; n++) {
    await expect(drop).toHaveAttribute('aria-disabled', 'false')
    await page.getByLabel('Answer sheet files').setInputFiles({
      name: `booklet-${n}.pdf`,
      mimeType: 'application/pdf',
      buffer: Buffer.from(`%PDF-1.7 synthetic ${n}`),
    })
    await expect(page.getByRole('region', { name: 'Upload queue' })).toContainText(
      `Queue (${n} of 5)`,
    )
  }
  await expect(drop).toHaveAttribute('aria-disabled', 'true')
  await expect(page.getByText(/You already have 5 booklets waiting/)).toBeVisible()
  await expect(
    page.getByRole('region', { name: 'Uploaded student answer submissions' }).getByRole('article'),
  ).toHaveCount(5)
})
