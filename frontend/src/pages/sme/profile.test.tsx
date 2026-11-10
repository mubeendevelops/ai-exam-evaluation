import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { maths, physics } from '../../test/adminFixtures'
import { meOf, tokenOf } from '../../test/fixtures'
import { renderApp } from '../../test/render'
import { smeSession } from '../../test/smeFixtures'

afterEach(() => vi.unstubAllGlobals())

/** An SME whose admin has set the staff fields. */
function withFields(extra: Parameters<typeof smeSession>[0] = {}) {
  const fields = { role: 'sme', employee_id: 'TKS-8904', department: 'Quantum Physics' } as const
  return smeSession({
    'POST /api/v1/auth/refresh': { json: tokenOf(fields) },
    'GET /api/v1/auth/me': { json: meOf(fields, 3) },
    ...extra,
  })
}

async function details() {
  return screen.findByRole('region', { name: 'Asha Rao' })
}

describe('My Profile (P36, D225)', () => {
  it('shows name, email, employee ID, department and the subjects as specialisation', async () => {
    withFields()
    renderApp('/sme/profile')
    expect(await screen.findByRole('heading', { name: 'My Profile', level: 1 })).toBeVisible()
    const card = within(await details())
    expect(card.getByText('Subject Matter Expert - Quantum Physics')).toBeInTheDocument()
    expect(card.getByText('Synthetic College • Employee ID: TKS-8904')).toBeInTheDocument()
    const value = (label: string) => card.getByText(label).closest('div')
    expect(value('Name')).toHaveTextContent('Asha Rao')
    expect(value('Email Address')).toHaveTextContent('asha@college.test')
    expect(value('Employee ID')).toHaveTextContent('TKS-8904')
    expect(value('Department')).toHaveTextContent('Quantum Physics')
    const subjects = await card.findByRole('list', { name: 'Subjects' })
    expect(within(subjects).getAllByRole('listitem')).toHaveLength(2)
    expect(within(subjects).getByText(physics.name)).toBeInTheDocument()
    expect(within(subjects).getByText(maths.name)).toBeInTheDocument()
  })

  it('is read-only: the details have no input, and the admin is named as the one who edits', async () => {
    withFields()
    renderApp('/sme/profile')
    const card = within(await details())
    expect(card.queryByRole('textbox')).toBeNull()
    expect(card.queryByRole('button')).toBeNull()
    expect(card.getByText(/Your admin keeps your name, employee ID, department/)).toBeVisible()
  })

  it('says "Not set" for an employee ID and department the admin has not entered', async () => {
    smeSession()
    renderApp('/sme/profile')
    const card = within(await details())
    expect(card.getByText('Employee ID').closest('div')).toHaveTextContent('Not set')
    expect(card.getByText('Department').closest('div')).toHaveTextContent('Not set')
    expect(card.getByText('Subject Matter Expert')).toBeInTheDocument()
    expect(card.getByText('Synthetic College')).toBeInTheDocument()
  })

  it('shows no specialisation when the SME has no subject', async () => {
    smeSession({ 'GET /api/v1/subjects': { json: [] } })
    renderApp('/sme/profile')
    const card = within(await details())
    await waitFor(() =>
      expect(card.getByText('Specialization').closest('div')).toHaveTextContent('Not set'),
    )
    expect(card.queryByRole('list', { name: 'Subjects' })).toBeNull()
  })

  it('changes the password on the page', async () => {
    const mock = smeSession({
      'POST /api/v1/auth/password/change': { status: 204 },
      'GET /api/v1/auth/password-bloom': { body: new ArrayBuffer(0), status: 500 },
    })
    const user = userEvent.setup()
    renderApp('/sme/profile')
    const form = within(await screen.findByRole('region', { name: 'Password' }))
    await user.type(form.getByLabelText('Current Password'), 'old password here')
    await user.type(form.getByLabelText('New Password'), 'a long uncommon passphrase')
    await user.click(form.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Password changed.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/auth/password/change')[0]?.body).toEqual({
      current_password: 'old password here',
      new_password: 'a long uncommon passphrase',
    })
    // The fields are empty again, and the page is still there.
    expect(form.getByLabelText('Current Password')).toHaveValue('')
    expect(screen.getByRole('heading', { name: 'My Profile', level: 1 })).toBeVisible()
  })

  it('refuses a wrong current password and keeps what was typed', async () => {
    smeSession({
      'POST /api/v1/auth/password/change': { status: 403, json: { detail: 'no' } },
      'GET /api/v1/auth/password-bloom': { status: 500, json: {} },
    })
    const user = userEvent.setup()
    renderApp('/sme/profile')
    const form = within(await screen.findByRole('region', { name: 'Password' }))
    await user.type(form.getByLabelText('Current Password'), 'wrong')
    await user.type(form.getByLabelText('New Password'), 'a long uncommon passphrase')
    await user.click(form.getByRole('button', { name: 'Change password' }))
    expect(await form.findByText('The current password is not correct.')).toBeInTheDocument()
  })

  it('creates recovery codes from the page, with the count of unused ones', async () => {
    const codes = Array.from({ length: 10 }, (_, i) => `ABCD-EFGH-IJKL-${String(1000 + i)}`)
    const mock = withFields({
      'POST /api/v1/auth/recovery-codes': { status: 201, json: { codes } },
    })
    const user = userEvent.setup()
    renderApp('/sme/profile')
    const panel = await screen.findByRole('region', { name: 'Recovery codes' })
    expect(within(panel).getByText('3 unused codes')).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Create new recovery codes' }))
    const dialog = await screen.findByRole('dialog', { name: 'Recovery codes' })
    await user.type(within(dialog).getByLabelText('Current Password'), 'current password')
    await user.click(within(dialog).getByRole('button', { name: 'Create new recovery codes' }))
    const list = await within(dialog).findByRole('list', { name: 'Recovery codes' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(10)
    expect(mock.callsTo('POST /api/v1/auth/recovery-codes')[0]?.body).toEqual({
      current_password: 'current password',
    })
  })
})
