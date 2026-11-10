import { expect, test, type Page } from '@playwright/test'
import { FakeApi } from './fakeApi'

async function signIn(page: Page, api = new FakeApi()) {
  await api.install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/) // an SME lands on My Assignments (P31)
  return api
}

async function link(page: Page, item: number, code: string) {
  const name = `Section 1 item ${item}`
  await page.getByRole('button', { name: `Show details of ${name}` }).click()
  await page.getByRole('button', { name: `Link the question of ${name}` }).click()
  const dialog = page.getByRole('dialog', { name: `Question for ${name}` })
  await dialog.getByRole('button', { name: `Link ${code}` }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByText(code, { exact: true })).toBeVisible()
}

test('from a schema: generate a paper, link its slots, set an exam code, preview it', async ({
  page,
}) => {
  const api = await signIn(page)
  await page
    .getByRole('navigation', { name: 'SME sections' })
    .getByRole('link', {
      name: 'Manage Schema',
    })
    .click()
  await page.getByRole('button', { name: 'Generate Question Paper from Physics Matrix' }).click()
  await expect(page).toHaveURL(/\/sme\/papers\/paper-\d+$/)
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText(
    'Manage Question Papers › Physics Matrix paper',
  )

  // A 4-mark slot takes the 4-mark question only.
  await page.getByRole('button', { name: 'Show details of Section 1 item 1' }).click()
  await page.getByRole('button', { name: 'Link the question of Section 1 item 1' }).click()
  const search = page.getByRole('dialog', { name: 'Question for Section 1 item 1' })
  await expect(search.getByRole('button', { name: 'Link PHY-Q2' })).toBeDisabled()
  await search.getByRole('button', { name: 'Link PHY-Q1' }).click()
  await expect(search).toBeHidden()
  await expect(page.getByText('PHY-Q1', { exact: true })).toBeVisible()
  await link(page, 2, 'PHY-Q2')

  await page.getByLabel('Exam Code').fill('PHY-MID-26')
  const json = page.getByTestId('schema-json')
  await expect(json).toContainText('"exam_code": "PHY-MID-26"')
  await expect(json).toContainText('"question_id": "q-1"')
  await expect(json).toContainText('"question_id": "q-2"')
  await page.getByRole('button', { name: 'Save paper' }).click()
  await expect(page.getByText('Version 2 · saved')).toBeVisible()

  await page.getByRole('button', { name: 'Preview' }).click()
  const preview = page.getByRole('dialog', { name: 'Physics Matrix paper - Preview' })
  await expect(preview.getByRole('img', { name: 'Page 1 of 1' })).toBeVisible()
  await expect(preview.getByRole('group', { name: 'Zoom' })).toContainText('100%')
  await preview.getByRole('button', { name: 'Zoom in' }).click()
  await expect(preview.getByRole('group', { name: 'Zoom' })).toContainText('110%')
  // The canvas really holds the drawn page.
  const drawn = await preview
    .getByRole('img', { name: 'Page 1 of 1' })
    .evaluate((c) => (c as HTMLCanvasElement).width)
  expect(drawn).toBeGreaterThan(100)
  expect(api.pdfRequests.at(-1)).toMatch(/^paper-\d+ v2$/)

  // The paper is on Manage Question Papers now.
  await preview.getByRole('button', { name: 'Close', exact: true }).last().click()
  await page.getByRole('link', { name: 'Manage Question Papers' }).first().click()
  const card = page.getByRole('listitem', { name: 'Physics Matrix paper' })
  await expect(card).toContainText('Exam code PHY-MID-26')
})

test('a new paper from the dialog, laid out section by section', async ({ page }) => {
  await signIn(page)
  await page.goto('/sme/papers')
  await page
    .getByRole('button', { name: /Create Question Paper/ })
    .first()
    .click()
  const dialog = page.getByRole('dialog', { name: 'Create / Clone Question Paper' })
  await dialog.getByLabel('Question Paper Name').fill('Constitution paper')
  await dialog.getByLabel('Subject').selectOption({ label: 'Physics (PHY-501)' })
  await dialog.getByLabel('Popular Descriptor').fill('Mid-term')
  await dialog.getByRole('button', { name: 'Save & Generate Paper' }).click()
  await expect(page).toHaveURL(/\/sme\/papers\/paper-\d+$/)
  const json = page.getByTestId('schema-json')
  await expect(json).toContainText('"title": "Constitution paper"')
  await expect(json).toContainText('"descriptor": "Mid-term"')

  const sections: [number, string, string, string][] = [
    [1, '7', '2', '5'],
    [2, '7', '5', '4'],
    [3, '3', '10', '2'],
  ]
  for (const [n, count, marks, any] of sections) {
    if (n > 1) await page.getByRole('button', { name: 'Add Section' }).click()
    await page.getByLabel(`Section ${n} question count`).fill(count)
    await page.getByLabel(`Section ${n} marks per question`).fill(marks)
    await page.getByRole('button', { name: `Fill section ${n}` }).click()
    await page.getByLabel(`Section ${n} choice rule`).selectOption('any')
    await page.getByLabel(`Section ${n} N`, { exact: true }).fill(any)
  }
  await expect(page.getByText('50 Marks').first()).toBeVisible()
  await expect(json).toContainText('"total_marks": 50')
  await expect(json).toContainText('"rule": "any"')
  await expect(page.getByRole('status', { name: 'Schema status' })).toContainText('Valid')
})
