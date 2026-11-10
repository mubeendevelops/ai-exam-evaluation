import { expect, test, type Page } from '@playwright/test'
import { FakeApi } from './fakeApi'

async function signIn(page: Page) {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/) // an SME lands on My Assignments (P31)
  // The bank is not a tab (D165, P35): it is reached from Manage Question Papers.
  const nav = page.getByRole('navigation', { name: 'SME sections' })
  await expect(nav.getByRole('link', { name: 'Q&A DB' })).toHaveCount(0)
  await nav.getByRole('link', { name: 'Manage Question Papers' }).click()
  await page.getByRole('link', { name: 'Questions', exact: true }).click()
  await expect(page).toHaveURL(/\/sme\/questions$/)
}

test('browse, filter, open a question in the editor, and see the no-student-data rule', async ({
  page,
}) => {
  await signIn(page)
  await expect(page.getByRole('article', { name: 'Question PHY-Q1' })).toBeVisible()
  await expect(page.getByRole('article', { name: 'Question PHY-Q2' })).toBeVisible()

  await page.getByRole('combobox', { name: /Difficulty/ }).selectOption('hard')
  await expect(page.getByRole('article', { name: 'Question PHY-Q1' })).toBeHidden()
  await expect(page.getByRole('article', { name: 'Question PHY-Q2' })).toBeVisible()
  await page.getByRole('combobox', { name: /Difficulty/ }).selectOption('')

  await page.getByRole('button', { name: 'Open PHY-Q1' }).click()
  await expect(page).toHaveURL(/\/sme\/questions\/q-1$/)
  const form = page.getByRole('form', { name: 'Edit question' })
  await expect(form.getByRole('textbox', { name: 'Question code' })).toHaveValue('PHY-Q1')
  await expect(form.getByRole('region', { name: 'Rubric' })).toContainText(
    'Weights add up to 4 of 4 marks.',
  )

  await page.getByRole('button', { name: 'Upload Answer Key' }).click()
  const dialog = page.getByRole('dialog', { name: 'Upload Answer Key' })
  await expect(dialog).toContainText('Keys hold no student data')
  await expect(dialog.getByRole('button', { name: 'Upload answer key' })).toBeDisabled()
  await page.keyboard.press('Escape')

  // The search beside the editor finds the other question, to open or clone.
  const similar = page.getByRole('complementary', { name: 'Similar questions' })
  await similar.getByRole('searchbox').fill('resonance')
  await expect(similar.getByRole('link', { name: 'Open PHY-Q2' })).toBeVisible()
  await expect(similar.getByRole('link', { name: 'Clone PHY-Q2 as a start' })).toBeVisible()

  await page.goBack() // the browser's Back returns to the list
  await expect(page.getByRole('article', { name: 'Question PHY-Q2' })).toBeVisible()
})

test('a question the SME has not saved yet can be cloned from the search', async ({ page }) => {
  await signIn(page)
  await page.getByRole('button', { name: 'New question', exact: true }).click()
  await expect(page).toHaveURL(/\/sme\/questions\/new$/)
  const similar = page.getByRole('complementary', { name: 'Similar questions' })
  await similar.getByRole('searchbox').fill('lenz')
  await similar.getByRole('link', { name: 'Clone PHY-Q1 as a start' }).click()
  const form = page.getByRole('form', { name: 'New question' })
  await expect(form.getByRole('textbox', { name: 'Question Title / Text' })).toHaveValue(
    'State Lenz’s law.',
  )
  await expect(form.getByRole('textbox', { name: 'Question code' })).toHaveValue('')
})
