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
  await expect(page.getByText('The evaluation view is coming next')).toBeVisible()
})
