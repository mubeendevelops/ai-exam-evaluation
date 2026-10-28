import { expect, test, type Page } from '@playwright/test'
import { FakeApi } from './fakeApi'
import { FakeBooklets } from './fakeBooklets'

async function signIn(page: Page) {
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
}

test('the admin creates a unit and uploads its booklets; the SME opens one', async ({ page }) => {
  const api = new FakeApi({ role: 'admin' })
  await api.install(page)
  const booklets = new FakeBooklets({ unit: false })
  await booklets.install(page)

  // --- the admin: Upload & Assign Papers, no unit yet ---
  await signIn(page)
  await expect(page.getByRole('heading', { name: 'Dashboard Overview' })).toBeVisible()
  await page
    .getByRole('navigation', { name: 'Admin sections' })
    .getByRole('link', { name: 'Upload & Assign Papers' })
    .click()
  await expect(page.getByText('No units yet')).toBeVisible()

  // --- create the unit: subject, paper, year, due date, an entitled SME ---
  await page.getByRole('button', { name: /Create unit/ }).click()
  const dialog = page.getByRole('dialog', { name: 'Create a unit' })
  await dialog.getByLabel('Subject').selectOption({ label: 'Physics (PHY-501)' })
  await dialog.getByLabel('Paper').selectOption({ label: 'PHY-2026-MID · Mid-term Physics' })
  await dialog.getByLabel('Student year').fill('2024')
  await dialog.getByLabel('Due date').fill('2030-01-31')
  await dialog.getByLabel('SME').selectOption({ label: 'Prof. Asha Rao (asha@synthetic.test)' })
  await dialog.getByRole('button', { name: 'Create unit' }).click()
  await expect(page.getByText(/Unit U-0001 created; Prof. Asha Rao was emailed/)).toBeVisible()
  expect(booklets.unitRequests[0]).toMatchObject({ year: '2024', due_date: '2030-01-31' })

  // --- the unit's page: drop two PDFs named by roll number, one unknown ---
  await page.getByRole('link', { name: 'U-0001' }).click()
  await expect(page.getByRole('heading', { name: 'Unit U-0001' })).toBeVisible()
  await page.getByLabel('Answer sheet files').setInputFiles([
    { name: 'tst001.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7 a') },
    { name: 'NOBODY01.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7 b') },
  ])
  const results = page.getByRole('region', { name: 'Upload results' })
  await expect(results).toContainText('Queued')
  await expect(results).toContainText('No student with this roll number')
  await expect(results.getByRole('link', { name: 'Students' })).toHaveAttribute(
    'href',
    '/admin/upload/students',
  )
  await expect(page.getByRole('table', { name: 'Booklets of the unit' })).toContainText('TST001')
  expect(booklets.uploadRequests).toBe(1)

  // --- the SME signs in and opens the booklet from their unit ---
  await page.getByRole('button', { name: /Account menu for/ }).click()
  await page.getByRole('menuitem', { name: /Sign out/ }).click()
  api.role = 'sme'
  await signIn(page)
  await expect(page.getByRole('heading', { name: 'Questions & Answers Repository' })).toBeVisible()
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'AI Evaluation' })
    .click()
  await expect(page.getByLabel('Unit', { exact: true })).toContainText(
    'U-0001 · PHY-2026-MID · 2024',
  )
  const card = page.getByRole('article', { name: 'Submission Test Student One' })
  await expect(card).toContainText('USN: TST001')
  await card.getByRole('button', { name: /Run AI Eval|Open/ }).click()
  await expect(page).toHaveURL(/\/evaluate\?booklet=/)
  await expect(
    page
      .getByRole('list', { name: 'Evaluation steps' })
      .getByRole('button', { name: '2. AI Segmentation' }),
  ).toHaveAttribute('aria-current', 'step')
  await expect(page.getByRole('article', { name: /Question 1 Answer Clip/ })).toBeVisible({
    timeout: 20_000,
  })
})
