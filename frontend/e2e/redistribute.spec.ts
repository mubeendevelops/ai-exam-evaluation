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

test('the SME asks to move a unit; the admin transfers it from the inbox', async ({ page }) => {
  const api = new FakeApi({ role: 'sme' })
  await api.install(page)
  const booklets = new FakeBooklets()
  await booklets.install(page)

  // --- the SME delegates from My Assignments, suggesting a colleague (P32, D218) ---
  await signIn(page)
  await page.getByRole('button', { name: 'Delegate U-0001' }).click()
  const ask = page.getByRole('dialog', { name: /Delegate U-0001/ })
  await ask
    .getByLabel('Suggested replacement (optional)')
    .selectOption({ label: 'Prof. Kiran Shah' })
  await ask.getByLabel('Reason for transfer').fill('On leave from Monday')
  await ask.getByRole('button', { name: 'Submit request to admin' }).click()
  await expect(page.getByText('Reassignment requested')).toBeVisible()
  const mine = page.getByRole('region', { name: 'My reassignment requests' })
  await expect(mine).toContainText('Pending')

  // --- the admin: Redistribute Workload, the inbox, transfer with the reason prefilled ---
  await signOut(page)
  api.role = 'admin'
  booklets.viewer = 'admin'
  await signIn(page)
  await page
    .getByRole('navigation', { name: 'Admin sections' })
    .getByRole('link', { name: 'Redistribute Workload' })
    .click()
  const request = page.getByRole('listitem', { name: 'Request for U-0001' })
  await expect(request).toContainText('On leave from Monday')
  await expect(request).toContainText('Suggested: Prof. Kiran Shah')
  await expect(
    page.getByRole('region', { name: 'Units of Prof. Asha Rao' }).getByText('reassignment asked'),
  ).toBeVisible()
  await request.getByRole('button', { name: 'Transfer' }).click()
  const dialog = page.getByRole('dialog', { name: 'Transfer U-0001' })
  await expect(dialog.getByLabel('Reason for the transfer')).toHaveValue('On leave from Monday')
  await expect(dialog.getByLabel('New SME')).not.toContainText('Asha Rao')
  // The SME's suggestion is picked already; the admin may change it.
  await expect(dialog.getByLabel('New SME')).toHaveValue('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
  await dialog.getByRole('button', { name: 'Transfer unit' }).click()
  await expect(page.getByText(/U-0001 moved to Prof. Kiran Shah/)).toBeVisible()
  await expect(page.getByText('No request is waiting.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Units of Prof. Kiran Shah' })).toBeVisible()
  expect(booklets.transfers[0]).toMatchObject({
    reason: 'On leave from Monday',
    expected_version: 1,
  })

  // --- the first SME: the unit is gone, the request reads Transferred ---
  await signOut(page)
  api.role = 'sme'
  booklets.viewer = 'sme'
  await signIn(page)
  await expect(page.getByText('No units assigned to you')).toBeVisible()
  await expect(page.getByRole('region', { name: 'My reassignment requests' })).toContainText(
    'Transferred',
  )
})

test('the admin declines a request with a note, which the SME then reads', async ({ page }) => {
  const api = new FakeApi({ role: 'admin' })
  await api.install(page)
  const booklets = new FakeBooklets()
  booklets.viewer = 'admin'
  booklets.requests.push({
    id: 'req-1',
    requested_by: { id: '99999999-9999-4999-8999-999999999999', name: 'Prof. Asha Rao' },
    reason: 'Too many units',
    requested_at: '2026-10-28T10:00:00Z',
    state: 'pending',
    closed_at: null,
    note: '',
  })
  await booklets.install(page)
  await signIn(page)
  await page.goto('/admin/redistribute')
  const request = page.getByRole('listitem', { name: 'Request for U-0001' })
  await request.getByRole('button', { name: 'Decline' }).click()
  const dialog = page.getByRole('dialog', { name: 'Decline the request for U-0001' })
  await dialog.getByLabel('Note to the SME').fill('Nobody else is free this week')
  await dialog.getByRole('button', { name: 'Decline request' }).click()
  await expect(page.getByText('Request for U-0001 declined.')).toBeVisible()
  await page.getByLabel('Show').selectOption('declined')
  await expect(request).toContainText('Nobody else is free this week')

  await signOut(page)
  api.role = 'sme'
  booklets.viewer = 'sme'
  await signIn(page)
  const mine = page.getByRole('region', { name: 'My reassignment requests' })
  await expect(mine).toContainText('Declined')
  await expect(mine).toContainText("Admin's note: Nobody else is free this week")
  await expect(page.getByRole('button', { name: 'Delegate U-0001' })).toBeVisible()
})
