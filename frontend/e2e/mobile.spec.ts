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

  await go(page, 'Manage Question Papers')
  await expect(page).toHaveURL(/\/sme\/papers$/)
  await expect(sidebar).toBeHidden()
  expect(await overflow()).toBeLessThanOrEqual(0)
})

test('on a phone the paper designer, with an OR pair, sub-parts, the picker and the preview open, does not scroll sideways', async ({
  page,
}) => {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/) // an SME lands on My Assignments (P31)

  await go(page, 'Manage Schema')
  await page.getByRole('button', { name: 'Generate Question Paper from Physics Matrix' }).click()
  await expect(page).toHaveURL(/\/sme\/papers\/paper-\d+$/)
  await page.getByRole('button', { name: 'Add OR pair to section 1' }).click()
  await page.getByRole('button', { name: 'Show details of Section 1 item 3 alternative 1' }).click()
  await page.getByRole('button', { name: 'Add sub-part' }).click()
  await page.getByRole('button', { name: 'Add sub-part' }).click()
  await expect(
    page.getByRole('textbox', { name: 'Section 1 item 3 alternative 1 part 2 marks' }),
  ).toBeVisible()
  const overflow = () =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(await overflow()).toBeLessThanOrEqual(0)

  await page
    .getByRole('button', { name: 'Link the question of Section 1 item 3 alternative 1 part 1' })
    .click()
  const search = page.getByRole('dialog', {
    name: 'Question for Section 1 item 3 alternative 1 part 1',
  })
  await expect(search.getByRole('list', { name: 'Matching questions' })).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)
  await page.keyboard.press('Escape')

  await page.goto('/sme/papers')
  await page.getByRole('button', { name: 'Preview Physics Matrix paper' }).click()
  const preview = page.getByRole('dialog', { name: 'Physics Matrix paper - Preview' })
  await expect(preview.getByRole('img', { name: 'Page 1 of 1' })).toBeVisible()
  await preview.getByRole('button', { name: 'Zoom in' }).click()
  expect(await overflow()).toBeLessThanOrEqual(0)
})

test('on a phone the question list, the question editor with its search do not scroll sideways', async ({
  page,
}) => {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page).toHaveURL(/\/sme$/)
  await go(page, 'Manage Question Papers')
  await page.getByRole('link', { name: 'Questions', exact: true }).click()
  await expect(page.getByRole('article', { name: 'Question PHY-Q1' })).toBeVisible()
  const overflow = () =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(await overflow()).toBeLessThanOrEqual(0)

  await page.getByRole('button', { name: 'Open PHY-Q1' }).click()
  await expect(page.getByRole('form', { name: 'Edit question' })).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)

  // The search beside the editor drops below it on a phone, with its results.
  const similar = page.getByRole('complementary', { name: 'Similar questions' })
  await similar.getByRole('searchbox').fill('resonance')
  await expect(similar.getByRole('link', { name: 'Open PHY-Q2' })).toBeVisible()
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
  await page.getByRole('link', { name: 'Evaluate U-0001' }).click()

  const sideways = () =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  await expect(page.getByRole('row', { name: /Test Student One/ })).toBeVisible()
  expect(await sideways()).toBeLessThanOrEqual(0)

  await page.getByRole('link', { name: 'Open the booklet of Test Student One' }).click()
  await expect(page.getByRole('heading', { name: 'Test Student One' })).toBeVisible()
  expect(await sideways()).toBeLessThanOrEqual(0)
  await page.getByRole('link', { name: /Pages and segments/ }).click()
  const one = page.getByRole('article', { name: /Question 1 Answer Clip/ })
  await expect(one).toBeVisible({ timeout: 20_000 })
  await one.getByRole('button', { name: /Line 2, low OCR confidence/ }).click()
  await expect(one.getByRole('textbox', { name: 'Correct the text of this line' })).toBeVisible()
  expect(await sideways()).toBeLessThanOrEqual(0)
})
