import { expect, test } from '@playwright/test'
import { FakeApi } from './fakeApi'

test('on a phone the sub-bar carries the three tabs and nothing scrolls sideways', async ({
  page,
}) => {
  await new FakeApi().install(page)
  await page.goto('/')
  await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
  await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
  await page.getByLabel('Security Access Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
  await expect(page.getByRole('heading', { name: 'Questions & Answers Repository' })).toBeVisible()

  const mobile = page.getByRole('navigation', { name: 'Main (mobile)' })
  await expect(mobile).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main', exact: true })).toBeHidden()
  await mobile.getByRole('link', { name: 'AI Eval' }).click()
  await expect(page).toHaveURL(/\/evaluate$/)
  await mobile.getByRole('link', { name: 'Schema' }).click()
  await expect(page).toHaveURL(/\/schema$/)

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
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
  await expect(page.getByRole('heading', { name: 'Questions & Answers Repository' })).toBeVisible()

  await page
    .getByRole('navigation', { name: 'Main (mobile)' })
    .getByRole('link', { name: 'Schema' })
    .click()
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
