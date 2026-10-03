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

test('browse, filter, open a question, and see the no-student-data rule', async ({ page }) => {
  await signIn(page)
  await expect(page.getByRole('article', { name: 'Question PHY-Q1' })).toBeVisible()
  await expect(page.getByRole('article', { name: 'Question PHY-Q2' })).toBeVisible()

  await page.getByRole('combobox', { name: /Difficulty/ }).selectOption('hard')
  await expect(page.getByRole('article', { name: 'Question PHY-Q1' })).toBeHidden()
  await expect(page.getByRole('article', { name: 'Question PHY-Q2' })).toBeVisible()
  await page.getByRole('combobox', { name: /Difficulty/ }).selectOption('')

  await page.getByRole('button', { name: 'Open PHY-Q1' }).click()
  await expect(page).toHaveURL(/\/qna\?question=q-1$/)
  await expect(page.getByRole('region', { name: 'Rubric' })).toContainText('States the law')
  await expect(page.getByRole('region', { name: 'Rubric' })).toContainText(
    'Weights add up to 4 of 4 marks.',
  )

  await page.getByRole('button', { name: 'Upload Answer Key' }).click()
  const dialog = page.getByRole('dialog', { name: 'Upload Answer Key' })
  await expect(dialog).toContainText('Keys hold no student data')
  await expect(dialog.getByRole('button', { name: 'Upload answer key' })).toBeDisabled()
  await page.keyboard.press('Escape')

  await page.goBack() // the browser's Back returns to the list
  await expect(page.getByRole('article', { name: 'Question PHY-Q2' })).toBeVisible()
})
