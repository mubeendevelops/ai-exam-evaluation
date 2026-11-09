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

test('the SME evaluates from My Assignments and delegates with a suggestion the admin sees pre-filled', async ({
  page,
}) => {
  const api = new FakeApi({ role: 'sme' })
  await api.install(page)
  const booklets = new FakeBooklets()
  booklets.processed()
  await booklets.install(page)

  // --- the SME lands on My Assignments and opens the unit's student list (P32, D217) ---
  await signIn(page)
  await expect(page).toHaveURL(/\/sme$/)
  await page.getByRole('link', { name: 'Evaluate U-0001' }).click()
  await expect(page).toHaveURL(/\/sme\/units\//)
  await page.getByLabel('Search student name or roll no.').fill('TST001')
  await expect(page).toHaveURL(/q=TST001/)
  const row = page.getByRole('row', { name: /Test Student One/ })
  await expect(row).toContainText('Ready')
  await expect(row).toContainText('0 of 2')

  // --- a student: the question list, then one answer in step 3 ---
  await row.getByRole('link', { name: 'Review questions of Test Student One' }).click()
  const questions = page.getByRole('list', { name: 'Questions' })
  await expect(questions).toContainText('PHY-Q1')
  await expect(questions).toContainText('AI suggested: 1 / 3')
  await questions.getByRole('link', { name: 'Question 1: Suggested' }).click()
  await expect(page).toHaveURL(/step=3&answer=1/)
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Q1')
  await page
    .getByRole('region', { name: 'Panel C: SME Final Grading' })
    .getByRole('button', { name: 'Approve answer' })
    .click()
  await expect(page.getByText('Question 1 approved.')).toBeVisible()
  expect(booklets.decided.map((d) => d.call)).toEqual(['approve 1'])

  // --- back to the student list from the breadcrumb: the filter is kept ---
  await page
    .getByRole('navigation', { name: 'Breadcrumb' })
    .getByRole('link', { name: 'U-0001' })
    .click()
  await expect(page).toHaveURL(/\/sme\/units\/.*q=TST001/)
  await expect(page.getByLabel('Search student name or roll no.')).toHaveValue('TST001')
  await expect(page.getByRole('row', { name: /Test Student One/ })).toContainText('1 of 2')

  // --- Delegate with a suggested replacement (D218) ---
  await page.getByRole('button', { name: 'Delegate' }).click()
  const ask = page.getByRole('dialog', { name: /Delegate U-0001/ })
  await expect(ask).toContainText('Mid-term Physics')
  const pick = ask.getByLabel('Suggested replacement (optional)')
  await expect(pick).not.toContainText('Asha Rao') // never the SME themselves
  await pick.selectOption({ label: 'Prof. Kiran Shah' })
  await ask.getByLabel('Reason for transfer').fill('Topic outside my area')
  await ask.getByRole('button', { name: 'Submit request to admin' }).click()
  await expect(page.getByText('Reassignment requested')).toBeVisible()
  expect(booklets.requests[0]).toMatchObject({
    reason: 'Topic outside my area',
    suggested_to: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  })

  // --- the admin sees the suggestion and the transfer dialog pre-filled with it ---
  await page.getByRole('button', { name: /Account menu for/ }).click()
  await page.getByRole('menuitem', { name: /Sign out/ }).click()
  api.role = 'admin'
  booklets.viewer = 'admin'
  await signIn(page)
  await page.goto('/admin/redistribute')
  const request = page.getByRole('listitem', { name: 'Request for U-0001' })
  await expect(request).toContainText('Suggested: Prof. Kiran Shah')
  await request.getByRole('button', { name: 'Transfer' }).click()
  const dialog = page.getByRole('dialog', { name: 'Transfer U-0001' })
  await expect(dialog.getByLabel('New SME')).toHaveValue('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
  await expect(dialog).toContainText('Prof. Kiran Shah was suggested by Prof. Asha Rao')
  await expect(dialog.getByLabel('Reason for the transfer')).toHaveValue('Topic outside my area')
})
