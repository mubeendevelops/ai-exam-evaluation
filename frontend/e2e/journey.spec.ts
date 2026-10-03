import { expect, test } from '@playwright/test'
import { FakeApi } from './fakeApi'

const PASSWORD = 'an uncommon long passphrase'

test.describe('visitor journey', () => {
  test('register (approval stubbed), verify email, sign in, switch tabs, sign out', async ({
    page,
  }) => {
    const api = new FakeApi()
    await api.install(page)

    // --- register ---
    await page.goto('/')
    await expect(page.getByRole('heading', { name: /Customer Sign In/ })).toBeVisible()
    await page.getByRole('button', { name: 'Register' }).click()
    const dialog = page.getByRole('dialog', { name: 'Create Cloud Workspace' })

    const id = dialog.getByLabel('Institution ID / Org Domain ID')
    await id.fill('bad id')
    await expect(dialog.getByText(/Invalid Format/)).toBeVisible()
    await id.fill('SYNTH_COLLEGE')
    await dialog.getByRole('button', { name: 'Check' }).click()
    await expect(dialog.getByText(/ALREADY TAKEN/)).toBeVisible()
    await id.fill('NEW_COLLEGE')
    await dialog.getByRole('button', { name: 'Check' }).click()
    await expect(dialog.getByText('Org ID "NEW_COLLEGE" is AVAILABLE!')).toBeVisible()

    await dialog.getByLabel('College / Institution Name').fill('New College')
    await dialog.getByLabel('Admin Full Name').fill('Dr. New Admin')
    await dialog.getByLabel('Official Email').fill('admin@new.test')
    await dialog.getByLabel('Security Password').fill('short')
    await expect(dialog.getByTestId('strength-status')).toHaveText('TOO SHORT')
    await dialog.getByLabel('Security Password').fill(PASSWORD)
    await expect(dialog.getByTestId('strength-status')).toHaveText('HIGH SECURE')
    await dialog.getByRole('button', { name: /Complete Registration/ }).click()
    const done = page.getByRole('dialog', { name: 'Registration received' })
    await expect(done.getByText('Check your inbox to verify your email')).toBeVisible()
    await done.getByRole('button', { name: 'Close', exact: true }).last().click()
    await expect(page.getByRole('dialog')).toHaveCount(0)

    // --- verify email (the link from the console mail) ---
    await page.goto('/verify-email?token=college.verify-NEW_COLLEGE')
    await expect(page.getByRole('heading', { name: 'Email verified' })).toBeVisible()
    await expect(page.getByText(/Tarn operator now reviews/)).toBeVisible()
    await expect(page).toHaveURL(/\/verify-email$/) // the token is gone from the address bar

    // Not active yet: sign-in is refused until the operator approves.
    await page.goto('/')
    const fillSignIn = async () => {
      await page.getByLabel('Institution / Org Domain ID').fill('new_college')
      await page.getByLabel('Username / Evaluator Email').fill('admin@new.test')
      await page.getByLabel('Security Access Password').fill(PASSWORD)
      await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
    }
    await fillSignIn()
    await expect(page.getByRole('alert')).toContainText('Sign-in failed')

    // --- operator approves (stubbed), then sign in ---
    api.approve('NEW_COLLEGE')
    await fillSignIn()
    await expect(
      page.getByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeVisible()
    await expect(page.getByRole('button', { name: /Account menu for Dr. New Admin/ })).toHaveText(
      'DA',
    )

    // --- switch tabs ---
    const nav = page.getByRole('navigation', { name: 'Main' })
    await nav.getByRole('link', { name: 'AI Evaluation' }).click()
    await expect(page).toHaveURL(/\/evaluate$/)
    await expect(
      page.getByRole('heading', { name: 'AI Automated Answer Evaluation Pipeline' }),
    ).toBeVisible()
    await nav.getByRole('link', { name: 'Schema Designer' }).click()
    await expect(page).toHaveURL(/\/schema$/)
    await expect(
      page.getByRole('heading', { name: 'Question Paper Studio & Schema Engine' }),
    ).toBeVisible()
    await nav.getByRole('link', { name: 'Q&A DB' }).click()
    await expect(page).toHaveURL(/\/qna$/)

    // The status pill shows the real API and worker state.
    await expect(page.getByRole('status').filter({ hasText: 'API & worker online' })).toBeVisible()

    // --- a reload keeps the session (refresh cookie), then sign out ---
    await page.reload()
    await expect(
      page.getByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeVisible()
    await page.getByRole('button', { name: /Account menu/ }).click()
    await page.getByRole('menuitem', { name: /Sign out/ }).click()
    await expect(page.getByRole('heading', { name: /Customer Sign In/ })).toBeVisible()
    await page.goto('/qna')
    await expect(page.getByRole('heading', { name: /Customer Sign In/ })).toBeVisible() // guarded again
  })

  test('forgot password, then reset with the emailed link', async ({ page }) => {
    const api = new FakeApi()
    await api.install(page)
    await page.goto('/')
    await page.getByRole('link', { name: 'Forgot Password?' }).click()
    await expect(page.getByRole('heading', { name: 'Forgot your password?' })).toBeVisible()

    await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
    await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
    await page.getByRole('button', { name: /Email me a reset link/ }).click()
    await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible()
    await expect(page.getByText(/If an account exists/)).toBeVisible()
    expect(api.forgotRequests).toEqual([
      { institution_id: 'SYNTH_COLLEGE', email: 'admin@synthetic.test' },
    ])

    // The same answer for an account that does not exist.
    await page.goto('/forgot-password')
    await page.getByLabel('Institution / Org Domain ID').fill('NOBODY')
    await page.getByLabel('Username / Evaluator Email').fill('nobody@nowhere.test')
    await page.getByRole('button', { name: /Email me a reset link/ }).click()
    await expect(page.getByText(/If an account exists/)).toBeVisible()

    // The link in the email.
    await page.goto('/reset-password?token=college.reset-token')
    await expect(page).toHaveURL(/\/reset-password$/)
    await page.getByLabel('New Password', { exact: true }).fill(PASSWORD)
    await page.getByLabel('Confirm Password').fill(PASSWORD)
    await page.getByRole('button', { name: 'Update password' }).click()
    await expect(page.getByRole('heading', { name: 'Password updated' })).toBeVisible()

    // The link works once.
    await page.goto('/reset-password?token=college.reset-token')
    await page.getByLabel('New Password', { exact: true }).fill(PASSWORD)
    await page.getByLabel('Confirm Password').fill(PASSWORD)
    await page.getByRole('button', { name: 'Update password' }).click()
    await expect(page.getByText(/invalid, has expired, or was already used/)).toBeVisible()

    // And the new password signs in.
    await page.goto('/')
    await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
    await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
    await page.getByLabel('Security Access Password').fill(PASSWORD)
    await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
    await expect(
      page.getByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeVisible()
  })

  test('the landing page demos open as dialogs and close with Escape', async ({ page }) => {
    await new FakeApi().install(page)
    await page.goto('/')
    await page.getByRole('button', { name: /Intelligent Assessment Assistant/ }).click()
    await expect(
      page.getByRole('dialog', { name: /Tri-Window AI Evaluation Workstation/ }),
    ).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
  })
})
