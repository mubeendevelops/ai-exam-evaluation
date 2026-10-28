import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  accounts,
  adminSession,
  asha,
  ashaAccount,
  kiran,
  maths,
  physics,
  raviAccount,
  unitOf,
} from '../../test/adminFixtures'
import { renderApp } from '../../test/render'

afterEach(() => vi.unstubAllGlobals())

async function rowOf(name: string) {
  const row = (await screen.findByRole('rowheader', { name })).closest('tr')
  if (!row) throw new Error('no row')
  return within(row)
}

describe('SME directory', () => {
  it('lists SMEs only, with subjects as chips and Active / Suspended status', async () => {
    adminSession()
    renderApp('/admin/smes')
    const asha = await rowOf('Asha Rao')
    expect(asha.getByText('asha@college.test')).toBeInTheDocument()
    expect(asha.getByText('Physics')).toBeInTheDocument()
    expect(asha.getByText('Chemistry')).toBeInTheDocument() // retired, still granted
    expect(asha.getByText('Active')).toBeInTheDocument()
    expect(asha.getByText('locked')).toBeInTheDocument()
    expect(asha.getByText('reset required')).toBeInTheDocument()
    expect(asha.getByRole('button', { name: 'Suspend' })).toBeInTheDocument()
    expect(asha.getByRole('button', { name: 'Unlock' })).toBeInTheDocument()

    const ravi = await rowOf('Ravi Kumar')
    expect(ravi.getByText('Suspended')).toBeInTheDocument()
    expect(ravi.getByText('None')).toBeInTheDocument()
    expect(ravi.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
    expect(ravi.queryByRole('button', { name: 'Unlock' })).toBeNull()
    expect(screen.queryByRole('rowheader', { name: 'Dr. Admin' })).toBeNull()
  })

  it('shows an invited SME as such', async () => {
    adminSession({
      'GET /api/v1/accounts': {
        json: [{ ...ashaAccount, status: 'PENDING', locked_until: null, force_reset: false }],
      },
    })
    renderApp('/admin/smes')
    expect((await rowOf('Asha Rao')).getByText('Invited')).toBeInTheDocument()
  })

  it('searches by name, email or subject', async () => {
    adminSession()
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await rowOf('Asha Rao')
    const search = screen.getByLabelText('Search SMEs')
    await user.type(search, 'physics')
    expect(screen.getByRole('rowheader', { name: 'Asha Rao' })).toBeInTheDocument()
    expect(screen.queryByRole('rowheader', { name: 'Ravi Kumar' })).toBeNull()
    await user.clear(search)
    await user.type(search, 'ravi@')
    expect(screen.queryByRole('rowheader', { name: 'Asha Rao' })).toBeNull()
    await user.clear(search)
    await user.type(search, 'zzz')
    expect(screen.getByText('No SME matches.')).toBeInTheDocument()
  })

  it('adds an SME with a subject and sends the invitation', async () => {
    const mock = adminSession({ 'POST /api/v1/accounts': { status: 201, json: ashaAccount } })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click(await screen.findByRole('button', { name: 'Add SME' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add SME' })
    // Retired subjects are not offered.
    expect(within(dialog).queryByLabelText(/Chemistry/)).toBeNull()
    await user.type(within(dialog).getByLabelText('Full Name'), 'Prof. New Person')
    await user.type(within(dialog).getByLabelText('Email'), 'new@college.test')
    await user.click(within(dialog).getByRole('button', { name: 'Send invitation' }))
    expect(await within(dialog).findByText('Choose at least one subject.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts')).toHaveLength(0)

    await user.click(within(dialog).getByLabelText(/Mathematics/))
    await user.click(within(dialog).getByRole('button', { name: 'Send invitation' }))
    expect(await screen.findByText('Invitation sent to new@college.test.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts')[0]?.body).toEqual({
      display_name: 'Prof. New Person',
      email: 'new@college.test',
      subject_ids: [maths.id],
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('keeps the dialog open and says why when the email is taken', async () => {
    adminSession({
      'POST /api/v1/accounts': {
        status: 409,
        json: { detail: 'That email already has an account.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click(await screen.findByRole('button', { name: 'Add SME' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add SME' })
    await user.type(within(dialog).getByLabelText('Full Name'), 'Someone')
    await user.type(within(dialog).getByLabelText('Email'), 'dup@college.test')
    await user.click(within(dialog).getByLabelText(/Physics/))
    await user.click(within(dialog).getByRole('button', { name: 'Send invitation' }))
    expect(
      await within(dialog).findByText('That email already has an account.'),
    ).toBeInTheDocument()
  })

  it('closes the add dialog with Escape and Cancel', async () => {
    adminSession()
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click(await screen.findByRole('button', { name: 'Add SME' }))
    await screen.findByRole('dialog', { name: 'Add SME' })
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Add SME' }))
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }),
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it("edits an SME's subjects with a checklist: grants what was ticked, revokes what was not", async () => {
    const mock = adminSession({
      [`POST /api/v1/accounts/t-1/subjects/${maths.id}`]: { json: { subject_ids: [] } },
      [`DELETE /api/v1/accounts/t-1/subjects/${physics.id}`]: { json: { subject_ids: [] } },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Edit subjects' }))
    const dialog = await screen.findByRole('dialog', { name: 'Asha Rao' })
    expect(within(dialog).getByLabelText(/Physics/)).toBeChecked()
    expect(within(dialog).getByLabelText(/Chemistry/)).toBeChecked() // retired but granted
    expect(within(dialog).getByLabelText(/Mathematics/)).not.toBeChecked()
    await user.click(within(dialog).getByLabelText(/Physics/))
    await user.click(within(dialog).getByLabelText(/Mathematics/))
    await user.click(within(dialog).getByRole('button', { name: 'Save subjects' }))
    expect(await screen.findByText('Subjects of Asha Rao saved.')).toBeInTheDocument()
    expect(mock.callsTo(`POST /api/v1/accounts/t-1/subjects/${maths.id}`)).toHaveLength(1)
    expect(mock.callsTo(`DELETE /api/v1/accounts/t-1/subjects/${physics.id}`)).toHaveLength(1)
    expect(mock.callsTo('POST /api/v1/accounts/t-1/subjects/sub-3')).toHaveLength(0)
  })

  it('moves the open units of a removed subject in the same step, or cancels', async () => {
    const mock = adminSession({
      'GET /api/v1/units': (call) => ({
        json: call.query.get('subject_id') === physics.id ? [unitOf()] : [],
      }),
      'GET /api/v1/units/smes': {
        json: [
          { id: asha.id, name: asha.display_name, email: asha.email },
          { id: kiran.id, name: kiran.display_name, email: kiran.email },
        ],
      },
      [`POST /api/v1/accounts/t-1/subjects/${physics.id}/revoke`]: {
        json: { user_id: 't-1', subject_ids: ['sub-3'] },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Edit subjects' }))
    let dialog = await screen.findByRole('dialog', { name: 'Asha Rao' })
    await user.click(within(dialog).getByLabelText(/Physics/))
    await user.click(within(dialog).getByRole('button', { name: 'Save subjects' }))
    // The transfer step: one unit to move; Cancel changes nothing.
    const target = await within(dialog).findByLabelText('New SME for U-0001')
    expect(within(target).queryByRole('option', { name: /Asha Rao/ })).toBeNull()
    const query = mock.callsTo('GET /api/v1/units').at(-1)?.query
    expect([query?.get('owner_id'), query?.get('state')]).toEqual(['t-1', 'open'])
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(mock.callsTo(`DELETE /api/v1/accounts/t-1/subjects/${physics.id}`)).toHaveLength(0)
    expect(mock.callsTo(`POST /api/v1/accounts/t-1/subjects/${physics.id}/revoke`)).toHaveLength(0)

    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Edit subjects' }))
    dialog = await screen.findByRole('dialog', { name: 'Asha Rao' })
    await user.click(within(dialog).getByLabelText(/Physics/))
    await user.click(within(dialog).getByRole('button', { name: 'Save subjects' }))
    const submit = await within(dialog).findByRole('button', { name: 'Remove and transfer' })
    await user.selectOptions(await within(dialog).findByLabelText('New SME for U-0001'), kiran.id)
    expect(submit).toBeDisabled() // a reason first
    await user.type(within(dialog).getByLabelText('Reason for the transfers'), 'Physics moves')
    await user.click(submit)
    expect(await screen.findByText(/1 unit transferred/)).toBeInTheDocument()
    expect(
      mock.callsTo(`POST /api/v1/accounts/t-1/subjects/${physics.id}/revoke`)[0]?.body,
    ).toEqual({ transfers: [{ unit_id: 'unit-1', owner_id: kiran.id }], reason: 'Physics moves' })
    expect(mock.callsTo(`DELETE /api/v1/accounts/t-1/subjects/${physics.id}`)).toHaveLength(0)
  })

  it('removes an SME after asking, and says why when it is refused', async () => {
    const mock = adminSession({
      'DELETE /api/v1/accounts/t-1': {
        status: 409,
        json: { detail: 'This SME owns open units (U-0001): transfer them before removing.' },
      },
      'DELETE /api/v1/accounts/t-2': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Remove' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this SME?' })
    await user.click(within(dialog).getByRole('button', { name: 'Remove SME' }))
    expect(await screen.findByText(/owns open units \(U-0001\)/)).toBeInTheDocument()

    await user.click((await rowOf('Ravi Kumar')).getByRole('button', { name: 'Remove' }))
    await user.click(screen.getByRole('button', { name: 'Remove SME' }))
    expect(await screen.findByText('SME removed from the directory.')).toBeInTheDocument()
    expect(mock.callsTo('DELETE /api/v1/accounts/t-2')).toHaveLength(1)
  })

  it('shows the reason when a grant is refused', async () => {
    adminSession({
      [`POST /api/v1/accounts/t-2/subjects/${physics.id}`]: {
        status: 422,
        json: { detail: 'No such subject.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Ravi Kumar')).getByRole('button', { name: 'Edit subjects' }))
    const dialog = await screen.findByRole('dialog', { name: 'Ravi Kumar' })
    await user.click(within(dialog).getByLabelText(/Physics/))
    await user.click(within(dialog).getByRole('button', { name: 'Save subjects' }))
    expect(await within(dialog).findByText('No such subject.')).toBeInTheDocument()
  })

  it('asks before suspending, then suspends; resume needs no question', async () => {
    const mock = adminSession({
      'POST /api/v1/accounts/t-1/disable': { json: ashaAccount.user },
      'POST /api/v1/accounts/t-2/enable': { json: raviAccount.user },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Suspend' }))
    const dialog = screen.getByRole('dialog', { name: 'Suspend this account?' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(mock.callsTo('POST /api/v1/accounts/t-1/disable')).toHaveLength(0)

    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Suspend' }))
    await user.click(screen.getByRole('button', { name: 'Suspend account' }))
    expect(await screen.findByText('Account suspended.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts/t-1/disable')).toHaveLength(1)

    await user.click((await rowOf('Ravi Kumar')).getByRole('button', { name: 'Resume' }))
    expect(await screen.findByText('Account resumed.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts/t-2/enable')).toHaveLength(1)
  })

  it('forces a password reset after asking, and unlocks', async () => {
    const mock = adminSession({
      'POST /api/v1/accounts/t-1/unlock': { status: 204 },
      'POST /api/v1/accounts/t-1/force-reset': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Unlock' }))
    expect(await screen.findByText('Account unlocked.')).toBeInTheDocument()
    await user.click((await rowOf('Asha Rao')).getByRole('button', { name: 'Force reset' }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Force reset' }),
    )
    expect(
      await screen.findByText(/Password reset required at the next sign-in/),
    ).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts/t-1/force-reset')).toHaveLength(1)
    expect(mock.callsTo('POST /api/v1/accounts/t-1/unlock')).toHaveLength(1)
  })

  it("shows the server's reason when an action is refused", async () => {
    adminSession({
      'POST /api/v1/accounts/t-2/enable': { status: 403, json: { detail: 'Not allowed.' } },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Ravi Kumar')).getByRole('button', { name: 'Resume' }))
    expect(await screen.findByText('Not allowed.')).toBeInTheDocument()
  })

  it('says so when there are no SMEs yet', async () => {
    adminSession({ 'GET /api/v1/accounts': { json: [accounts[0]] } })
    renderApp('/admin/smes')
    await waitFor(() =>
      expect(screen.getByText('No SMEs yet. Add one to send an invitation.')).toBeInTheDocument(),
    )
  })
})
