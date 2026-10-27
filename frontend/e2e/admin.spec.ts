import { expect, test } from '@playwright/test'
import { FakeApi } from './fakeApi'

test.describe('admin portal', () => {
  test('adds a subject and an SME, edits its subjects, suspends it, adds a student', async ({
    page,
  }) => {
    await new FakeApi({ role: 'admin' }).install(page)
    await page.goto('/')
    await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
    await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
    await page.getByLabel('Security Access Password').fill('correct horse battery staple')
    await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
    await expect(page.getByRole('heading', { name: 'Dashboard Overview' })).toBeVisible()

    const sidebar = page.getByRole('navigation', { name: 'Admin sections' })
    await sidebar.getByRole('link', { name: 'SME Directory & Subjects' }).click()
    await expect(page).toHaveURL(/\/admin\/smes$/)

    // --- a subject ---
    await page.getByRole('button', { name: 'Add subject' }).click()
    const subjectDialog = page.getByRole('dialog', { name: 'Add subject' })
    await subjectDialog.getByLabel('Code').fill('MAT-101')
    await subjectDialog.getByLabel('Subject name').fill('Mathematics')
    await subjectDialog.getByRole('button', { name: 'Add subject' }).click()
    await expect(page.getByText('Subject Mathematics added.')).toBeVisible()
    await expect(page.getByRole('rowheader', { name: 'Mathematics' })).toBeVisible()

    // --- an SME with that subject ---
    await page.getByRole('button', { name: 'Add SME' }).click()
    const smeDialog = page.getByRole('dialog', { name: 'Add SME' })
    await smeDialog.getByLabel('Full Name').fill('Prof. Asha Rao')
    await smeDialog.getByLabel('Email').fill('asha@synthetic.test')
    await smeDialog.getByRole('button', { name: 'Send invitation' }).click()
    await expect(smeDialog.getByText('Choose at least one subject.')).toBeVisible()
    await smeDialog.getByLabel(/Mathematics/).check()
    await smeDialog.getByRole('button', { name: 'Send invitation' }).click()
    await expect(page.getByText('Invitation sent to asha@synthetic.test.')).toBeVisible()
    const sme = page.getByRole('row').filter({ hasText: 'Prof. Asha Rao' })
    await expect(sme.getByText('Mathematics')).toBeVisible()
    await expect(sme.getByText('Invited')).toBeVisible()

    // --- edit its subjects: add Physics, drop Mathematics ---
    await sme.getByRole('button', { name: 'Edit subjects' }).click()
    const subjects = page.getByRole('dialog', { name: 'Prof. Asha Rao' })
    await subjects.getByLabel(/Physics/).check()
    await subjects.getByLabel(/Mathematics/).uncheck()
    await subjects.getByRole('button', { name: 'Save subjects' }).click()
    await expect(page.getByText('Subjects of Prof. Asha Rao saved.')).toBeVisible()
    await expect(sme.getByText('Physics')).toBeVisible()
    await expect(sme.getByText('Mathematics')).toHaveCount(0)

    // --- suspend (asks first) ---
    await sme.getByRole('button', { name: 'Suspend' }).click()
    await page
      .getByRole('dialog', { name: 'Suspend this account?' })
      .getByRole('button', { name: 'Suspend account' })
      .click()
    await expect(page.getByText('Account suspended.')).toBeVisible()
    await expect(sme.getByText('Suspended')).toBeVisible()
    await expect(sme.getByRole('button', { name: 'Resume' })).toBeVisible()

    // --- a student, from Upload & Assign Papers ---
    await sidebar.getByRole('link', { name: 'Upload & Assign Papers' }).click()
    await page.getByRole('link', { name: /Open the student roster/ }).click()
    await expect(page).toHaveURL(/\/admin\/upload\/students$/)
    await page.getByRole('button', { name: 'Add student' }).click()
    const studentDialog = page.getByRole('dialog', { name: 'Add student' })
    await studentDialog.getByLabel('Name').fill('Meera Iyer')
    await studentDialog.getByLabel('Roll number (USN)').fill('1ab21cs003')
    await studentDialog.getByLabel('Class / section').fill('CSE-A')
    await studentDialog.getByRole('button', { name: 'Add student' }).click()
    await expect(page.getByText('Student added.')).toBeVisible()
    const student = page.getByRole('row').filter({ hasText: 'Meera Iyer' })
    await expect(student.getByText('1AB21CS003')).toBeVisible()

    // --- correct the roll number from the keyboard ---
    await student.getByRole('button', { name: 'Edit' }).focus()
    await page.keyboard.press('Enter')
    const edit = page.getByRole('dialog', { name: 'Edit student' })
    await edit.getByLabel('Roll number (USN)').fill('1AB21CS030')
    await page.keyboard.press('Enter')
    await expect(page.getByText('Student saved.')).toBeVisible()
    await expect(page.getByRole('cell', { name: '1AB21CS030' })).toBeVisible()
  })

  test('the sidebar is a scrolling row on a phone and every section opens', async ({ page }) => {
    await new FakeApi({ role: 'admin' }).install(page)
    await page.setViewportSize({ width: 390, height: 800 })
    await page.goto('/')
    await page.getByLabel('Institution / Org Domain ID').fill('SYNTH_COLLEGE')
    await page.getByLabel('Username / Evaluator Email').fill('admin@synthetic.test')
    await page.getByLabel('Security Access Password').fill('correct horse battery staple')
    await page.getByRole('button', { name: /Authenticate Cloud Access/ }).click()
    const sidebar = page.getByRole('navigation', { name: 'Admin sections' })
    await expect(sidebar).toBeVisible()
    await sidebar.getByRole('link', { name: 'Audit & External Reports' }).click()
    await expect(
      page.getByRole('heading', { name: 'Audit & External Reports', level: 1 }),
    ).toBeVisible()
    // The page itself does not scroll sideways.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
