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
