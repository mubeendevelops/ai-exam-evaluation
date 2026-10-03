import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { healthy, meOf, tokenOf, userOf } from '../test/fixtures'
import { mockApi, type Reply } from '../test/mockApi'
import { renderApp } from '../test/render'

afterEach(() => vi.unstubAllGlobals())

const admin = userOf({
  id: 'a-1',
  display_name: 'Dr. Admin',
  email: 'admin@college.test',
  role: 'admin',
})
const teacher = userOf({
  id: 't-1',
  display_name: 'Asha Rao',
  email: 'asha@college.test',
  role: 'teacher',
})
const disabled = userOf({
  id: 't-2',
  display_name: 'Ravi Kumar',
  email: 'ravi@college.test',
  role: 'teacher',
  active: false,
})

const accounts = [
  { user: admin, status: 'ACTIVE', locked_until: null, force_reset: false },
  { user: teacher, status: 'ACTIVE', locked_until: '2999-01-01T00:00:00Z', force_reset: true },
  { user: disabled, status: 'DISABLED', locked_until: null, force_reset: false },
]

function adminSession(extra: Record<string, Reply | ((c: never) => Reply)> = {}) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf({ role: 'admin' }) },
    'GET /api/v1/auth/me': { json: meOf({ role: 'admin' }) },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/accounts': { json: accounts },
    'GET /api/v1/students': { json: [] },
    ...(extra as Parameters<typeof mockApi>[0]),
  })
}

async function teachersRow(name: string) {
  const row = (await screen.findByRole('cell', { name })).closest('tr')
  if (!row) throw new Error('no row')
  return within(row)
}

describe('admin page', () => {
  it('lists the accounts and offers actions for teachers only', async () => {
    adminSession()
    renderApp('/admin')
    const adminRow = await teachersRow('Dr. Admin')
    expect(adminRow.queryByRole('button')).toBeNull()

    const ashaRow = await teachersRow('Asha Rao')
    expect(ashaRow.getByText('locked')).toBeInTheDocument()
    expect(ashaRow.getByText('reset required')).toBeInTheDocument()
    expect(ashaRow.getByRole('button', { name: 'Disable' })).toBeInTheDocument()
    expect(ashaRow.getByRole('button', { name: 'Force reset' })).toBeInTheDocument()
    expect(ashaRow.getByRole('button', { name: 'Unlock' })).toBeInTheDocument()

    const raviRow = await teachersRow('Ravi Kumar')
    expect(raviRow.getByRole('button', { name: 'Enable' })).toBeInTheDocument()
    expect(raviRow.queryByRole('button', { name: 'Unlock' })).toBeNull()
  })

  it('asks before disabling, then disables', async () => {
    const mock = adminSession({
      'POST /api/v1/accounts/t-1/disable': { json: accounts[1] },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    await user.click((await teachersRow('Asha Rao')).getByRole('button', { name: 'Disable' }))
    const dialog = screen.getByRole('dialog', { name: 'Disable this account?' })
    expect(mock.callsTo('POST /api/v1/accounts/t-1/disable')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(mock.callsTo('POST /api/v1/accounts/t-1/disable')).toHaveLength(0)

    await user.click((await teachersRow('Asha Rao')).getByRole('button', { name: 'Disable' }))
    await user.click(screen.getByRole('button', { name: 'Disable account' }))
    expect(await screen.findByText('Account disabled.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts/t-1/disable')).toHaveLength(1)
  })

  it('enables, unlocks and forces a reset', async () => {
    const mock = adminSession({
      'POST /api/v1/accounts/t-2/enable': { json: accounts[2] },
      'POST /api/v1/accounts/t-1/unlock': { status: 204 },
      'POST /api/v1/accounts/t-1/force-reset': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    await user.click((await teachersRow('Ravi Kumar')).getByRole('button', { name: 'Enable' }))
    expect(await screen.findByText('Account enabled.')).toBeInTheDocument()
    await user.click((await teachersRow('Asha Rao')).getByRole('button', { name: 'Unlock' }))
    expect(await screen.findByText('Account unlocked.')).toBeInTheDocument()
    await user.click((await teachersRow('Asha Rao')).getByRole('button', { name: 'Force reset' }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Force reset' }),
    )
    expect(
      await screen.findByText(/Password reset required at the next sign-in/),
    ).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts/t-1/force-reset')).toHaveLength(1)
    expect(mock.callsTo('POST /api/v1/accounts/t-2/enable')).toHaveLength(1)
    expect(mock.callsTo('POST /api/v1/accounts/t-1/unlock')).toHaveLength(1)
  })

  it("shows the server's reason when an action is refused", async () => {
    adminSession({
      'POST /api/v1/accounts/t-2/enable': { status: 403, json: { detail: 'Not allowed.' } },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    await user.click((await teachersRow('Ravi Kumar')).getByRole('button', { name: 'Enable' }))
    expect(await screen.findByText('Not allowed.')).toBeInTheDocument()
  })

  it('invites a teacher', async () => {
    const mock = adminSession({
      'POST /api/v1/accounts': { status: 201, json: accounts[1] },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    const form = await screen.findByRole('form', { name: 'Invite a teacher' })
    await user.type(within(form).getByLabelText('Full Name'), 'Prof. New Person')
    await user.type(within(form).getByLabelText('Email'), 'new@college.test')
    await user.click(within(form).getByRole('button', { name: 'Send invitation' }))
    expect(await screen.findByText('Invitation sent to new@college.test.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/accounts')[0]?.body).toEqual({
      display_name: 'Prof. New Person',
      email: 'new@college.test',
    })
    expect(within(form).getByLabelText('Full Name')).toHaveValue('')
  })

  it('reports a duplicate email on invitation', async () => {
    adminSession({
      'POST /api/v1/accounts': {
        status: 409,
        json: { detail: 'That email already has an account.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    const form = await screen.findByRole('form', { name: 'Invite a teacher' })
    await user.type(within(form).getByLabelText('Full Name'), 'Someone')
    await user.type(within(form).getByLabelText('Email'), 'dup@college.test')
    await user.click(within(form).getByRole('button', { name: 'Send invitation' }))
    expect(await within(form).findByText('That email already has an account.')).toBeInTheDocument()
  })
})

describe('roster', () => {
  const csv = new File(['name,usn,class/section\nAsha Rao,1AB21CS001,CSE-A\n'], 'roster.csv', {
    type: 'text/csv',
  })

  it('uploads the CSV as text/csv and reports what changed', async () => {
    const mock = adminSession({
      'POST /api/v1/roster/import': {
        json: { imported: true, rows: 1, created: 1, updated: 0, unchanged: 0, errors: [] },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    const form = await screen.findByRole('form', { name: 'Import the roster' })
    expect(within(form).getByRole('button', { name: 'Import roster' })).toBeDisabled()
    await user.upload(within(form).getByLabelText('Roster file'), csv)
    await user.click(within(form).getByRole('button', { name: 'Import roster' }))
    expect(await within(form).findByRole('status')).toHaveTextContent(
      'Imported 1 rows: 1 new, 0 updated, 0 unchanged.',
    )
    const call = mock.callsTo('POST /api/v1/roster/import')[0]!
    expect(call.headers.get('Content-Type')).toBe('text/csv')
    expect(call.body).toBe('name,usn,class/section\nAsha Rao,1AB21CS001,CSE-A\n')
  })

  it('lists every line error and says nothing was imported', async () => {
    adminSession({
      'POST /api/v1/roster/import': {
        json: {
          imported: false,
          rows: 2,
          created: 0,
          updated: 0,
          unchanged: 0,
          errors: [
            { line: 2, field: 'usn', message: 'USN is required.' },
            { line: 3, field: 'name', message: 'Name is required.' },
          ],
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    const form = await screen.findByRole('form', { name: 'Import the roster' })
    await user.upload(within(form).getByLabelText('Roster file'), csv)
    await user.click(within(form).getByRole('button', { name: 'Import roster' }))
    const alert = await within(form).findByRole('alert')
    expect(alert).toHaveTextContent('Nothing was imported. Fix these 2 problems and upload again.')
    expect(within(alert).getByText('USN is required.')).toBeInTheDocument()
    expect(within(alert).getByText('Name is required.')).toBeInTheDocument()
  })

  it('shows a refused file (wrong type or too big)', async () => {
    adminSession({
      'POST /api/v1/roster/import': {
        status: 415,
        json: { detail: 'Send the roster as text/csv.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin')
    const form = await screen.findByRole('form', { name: 'Import the roster' })
    await user.upload(within(form).getByLabelText('Roster file'), csv)
    await user.click(within(form).getByRole('button', { name: 'Import roster' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('Send the roster as text/csv.')
  })

  it('searches the roster by USN prefix or name', async () => {
    const mock = adminSession({
      'GET /api/v1/students': (call) => ({
        json: (call as never as { query: URLSearchParams }).query.get('q')
          ? [{ id: 's-1', name: 'Asha Rao', usn: '1AB21CS001', class_section: 'CSE-A' }]
          : [],
      }),
    })
    const user = userEvent.setup()
    renderApp('/admin')
    expect(await screen.findByText('The roster is empty. Upload a CSV first.')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search by USN or name'), '1AB21')
    expect(await screen.findByRole('cell', { name: '1AB21CS001' })).toBeInTheDocument()
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/students').some((c) => c.query.get('q') === '1AB21')).toBe(
        true,
      ),
    )
  })
})
