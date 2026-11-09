import { expect, test, type Page } from '@playwright/test'
import { FakeApi } from './fakeApi'
import { FakeBooklets } from './fakeBooklets'

/** On a phone the SME sidebar is a drawer behind the menu button (P31). */
async function go(page: Page, link: string) {
  await page.getByRole('button', { name: 'Menu', exact: true }).click()
  await page
    .getByRole('navigation', { name: 'SME sections' })
    .getByRole('link', { name: link })
    .click()
}

test('on a phone the sidebar is a drawer behind the menu button and nothing scrolls sideways', async ({
  page,
}) => {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/) // an SME lands on My Assignments (P31)

  const sidebar = page.getByRole('navigation', { name: 'SME sections' })
  await expect(sidebar).toBeHidden()
  const overflow = () =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(await overflow()).toBeLessThanOrEqual(0)

  const menu = page.getByRole('button', { name: 'Menu', exact: true })
  await menu.click()
  await expect(sidebar).toBeVisible()
  await expect(sidebar.getByRole('link').first()).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(sidebar).toBeHidden()
  await expect(menu).toBeFocused()

  await go(page, 'AI Evaluation')
  await expect(page).toHaveURL(/\/evaluate$/)
  await expect(sidebar).toBeHidden()
  await go(page, 'Schema Designer')
  await expect(page).toHaveURL(/\/schema$/)
  expect(await overflow()).toBeLessThanOrEqual(0)
})

test('on a phone the schema designer, with an OR pair and sub-parts open, does not scroll sideways', async ({
  page,
}) => {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/) // an SME lands on My Assignments (P31)

  await go(page, 'Schema Designer')
  await page.getByRole('button', { name: 'Add OR pair to section 1' }).click()
  await page.getByRole('button', { name: 'Show details of Section 1 item 6 alternative 1' }).click()
  await page.getByRole('button', { name: 'Add sub-part' }).click()
  await page.getByRole('button', { name: 'Add sub-part' }).click()
  await expect(
    page.getByRole('textbox', { name: 'Section 1 item 6 alternative 1 part 2 marks' }),
  ).toBeVisible()

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
})

test('on a phone the question bank, a question and the New Question modal do not scroll sideways', async ({
  page,
}) => {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/)
  await go(page, 'Q&A DB')
  await expect(page.getByRole('article', { name: 'Question PHY-Q1' })).toBeVisible()
  const overflow = () =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(await overflow()).toBeLessThanOrEqual(0)

  await page.getByRole('button', { name: 'Open PHY-Q1' }).click()
  await expect(page.getByRole('region', { name: 'Rubric' })).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)

  await page.getByRole('button', { name: 'Edit question' }).click()
  await expect(page.getByRole('dialog', { name: 'Edit PHY-Q1' })).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)
})

test('on a phone the submission list and segmentation steps do not scroll sideways', async ({
  page,
}) => {
  await new FakeApi().install(page)
  const booklets = new FakeBooklets()
  booklets.startUploaded() // the admin uploaded it into the SME's unit (P26)
  await booklets.install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/) // an SME lands on My Assignments (P31)
  await go(page, 'AI Evaluation')

  const sideways = () =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  await expect(page.getByRole('article', { name: 'Submission Test Student One' })).toBeVisible()
  expect(await sideways()).toBeLessThanOrEqual(0)

  await page.getByRole('button', { name: 'Run AI Eval' }).click()
  const one = page.getByRole('article', { name: /Question 1 Answer Clip/ })
  await expect(one).toBeVisible({ timeout: 20_000 })
  await one.getByRole('button', { name: /Line 2, low OCR confidence/ }).click()
  await expect(one.getByRole('textbox', { name: 'Correct the text of this line' })).toBeVisible()
  expect(await sideways()).toBeLessThanOrEqual(0)
})
