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

async function signOut(page: Page) {
  await page.getByRole('button', { name: /Account menu for/ }).click()
  await page.getByRole('menuitem', { name: /Sign out/ }).click()
}

async function evaluate(page: Page) {
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'AI Evaluation' })
    .click()
}

test('the SME submits, the admin returns, the SME resubmits, the Governor freezes', async ({
  page,
}) => {
  const api = new FakeApi({ role: 'sme' })
  await api.install(page)
  const booklets = new FakeBooklets()
  booklets.processed()
  await booklets.install(page)

  // --- the SME submits the unit, giving a reason for the booklet not marked (P28) ---
  await signIn(page)
  await evaluate(page)
  await page.getByRole('button', { name: 'Submit unit' }).click()
  let dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('0 of 1 booklets are marked.')
  await dialog.getByLabel(/^Reason for /).selectOption('absent')
  await dialog.getByRole('button', { name: 'Submit unit' }).click()
  await expect(page.getByText('Submitted to your admin', { exact: true })).toBeVisible()
  expect(booklets.submits[0]).toMatchObject({
    expected_version: 1,
    unmarked: [{ reason: 'absent', note: '' }],
  })

  // --- the admin returns it with a note ---
  await signOut(page)
  api.role = 'admin'
  booklets.viewer = 'admin'
  await signIn(page)
  await page.goto('/admin/upload/units/88888888-8888-4888-8888-888888888888')
  const panel = page.getByRole('region', { name: /Submission/ })
  await expect(panel).toContainText('not marked: Absent')
  await panel.getByRole('button', { name: /Return to SME/ }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Note to the SME').fill('Mark this one: the student sat late')
  await dialog.getByRole('button', { name: 'Return unit' }).click()
  await expect(page.getByText(/returned; Prof. Asha Rao was emailed/)).toBeVisible()

  // --- the SME reads the note and submits again ---
  await signOut(page)
  api.role = 'sme'
  booklets.viewer = 'sme'
  await signIn(page)
  await evaluate(page)
  await expect(page.getByText(/Your admin returned this unit: Mark this one/)).toBeVisible()
  await page.getByRole('button', { name: 'Submit unit' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel(/^Reason for /).selectOption('unreadable')
  await dialog.getByRole('button', { name: 'Submit unit' }).click()
  await expect(page.getByText('Submitted to your admin', { exact: true })).toBeVisible()

  // --- the Governor freezes and signs it off ---
  await signOut(page)
  api.role = 'governor'
  booklets.viewer = 'governor'
  await signIn(page)
  const waiting = page.getByRole('region', { name: 'Submitted, waiting for sign-off' })
  await waiting.getByRole('button', { name: /Freeze and sign off/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Freeze and sign off' }).click()
  await expect(page.getByText('U-0001 frozen and signed off.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Frozen and signed off' })).toContainText('U-0001')
  expect(booklets.unitState).toBe('frozen')
})
