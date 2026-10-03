import { expect, test, type Page } from '@playwright/test'
import { FakeApi } from './fakeApi'

async function signIn(page: Page) {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page.getByRole('heading', { name: 'Questions & Answers Repository' })).toBeVisible()
}

test('the schema designer builds QP-CI and the JSON panel follows every input', async ({
  page,
}) => {
  await signIn(page)
  await page.goto('/schema')
  await expect(
    page.getByRole('heading', { name: 'Generated Question Schema (JSON)' }),
  ).toBeVisible()
  const json = page.getByTestId('schema-json')

  await page.getByLabel('Exam Title / Header').fill('Constitution paper')
  await expect(json).toContainText('"title": "Constitution paper"')
  await expect(page.getByRole('status', { name: 'Schema status' })).toContainText('Valid')

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

  // Reset asks first, then clears.
  await page.getByRole('button', { name: 'Reset Form' }).click()
  await page.getByRole('button', { name: 'Yes, reset' }).click()
  await expect(json).toContainText('"title": ""')
})
