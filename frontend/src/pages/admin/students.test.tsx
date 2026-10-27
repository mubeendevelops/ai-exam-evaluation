import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { adminSession, students } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'

afterEach(() => vi.unstubAllGlobals())

async function rowOf(name: string) {
  const row = (await screen.findByRole('rowheader', { name })).closest('tr')
  if (!row) throw new Error('no row')
  return within(row)
}

describe('students', () => {
  it('lists the roster with USN, name and class / section', async () => {
    adminSession()
    renderApp('/admin/upload/students')
    const asha = await rowOf('Asha Student')
    expect(asha.getByText('1AB21CS001')).toBeInTheDocument()
    expect(asha.getByText('CSE-A')).toBeInTheDocument()
    expect(asha.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('searches the roster by USN prefix or name', async () => {
    const mock = adminSession({
      'GET /api/v1/students': (call) => ({
        json: call.query.get('q') ? [students[0]] : [],
      }),
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/students')
    expect(
      await screen.findByText('The roster is empty. Add a student or upload a CSV.'),
    ).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search by USN or name'), '1AB21')
    expect(await screen.findByRole('cell', { name: '1AB21CS001' })).toBeInTheDocument()
    await waitFor(() =>
      expect(mock.callsTo('GET /api/v1/students').some((c) => c.query.get('q') === '1AB21')).toBe(
        true,
      ),
    )
  })

  it('adds a student', async () => {
    const mock = adminSession({
      'POST /api/v1/students': { status: 201, json: { ...students[0], id: 's-9' } },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/students')
    await user.click(await screen.findByRole('button', { name: 'Add student' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add student' })
    await user.type(within(dialog).getByLabelText('Name'), 'Meera Iyer')
    await user.type(within(dialog).getByLabelText('Roll number (USN)'), '1ab21cs003')
    await user.type(within(dialog).getByLabelText('Class / section'), 'CSE-B')
    await user.click(within(dialog).getByRole('button', { name: 'Add student' }))
    expect(await screen.findByText('Student added.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/students')[0]?.body).toEqual({
      name: 'Meera Iyer',
      usn: '1ab21cs003',
      class_section: 'CSE-B',
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('edits name, USN and class / section, starting from the current values', async () => {
    const mock = adminSession({
      'PUT /api/v1/students/s-2': { json: { ...students[1], usn: '1AB21CS020' } },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/students')
    await user.click((await rowOf('Ravi Student')).getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit student' })
    expect(within(dialog).getByLabelText('Roll number (USN)')).toHaveValue('1AB21CS002')
    const usn = within(dialog).getByLabelText('Roll number (USN)')
    await user.clear(usn)
    await user.type(usn, '1AB21CS020')
    await user.type(within(dialog).getByLabelText('Class / section'), 'CSE-C')
    await user.click(within(dialog).getByRole('button', { name: 'Save student' }))
    expect(await screen.findByText('Student saved.')).toBeInTheDocument()
    expect(mock.callsTo('PUT /api/v1/students/s-2')[0]?.body).toEqual({
      name: 'Ravi Student',
      usn: '1AB21CS020',
      class_section: 'CSE-C',
    })
  })

  it('shows a USN that is already taken and keeps the dialog', async () => {
    adminSession({
      'PUT /api/v1/students/s-2': {
        status: 409,
        json: { detail: 'Another student with this USN is already on the roster.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/students')
    await user.click((await rowOf('Ravi Student')).getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit student' })
    await user.click(within(dialog).getByRole('button', { name: 'Save student' }))
    expect(
      await within(dialog).findByText('Another student with this USN is already on the roster.'),
    ).toBeInTheDocument()
  })

  it('shows a refused USN (not 4 to 20 letters and digits)', async () => {
    adminSession({
      'POST /api/v1/students': {
        status: 422,
        json: { detail: 'usn: USN must be 4 to 20 letters and digits.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/upload/students')
    await user.click(await screen.findByRole('button', { name: 'Add student' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add student' })
    await user.type(within(dialog).getByLabelText('Name'), 'Meera')
    await user.type(within(dialog).getByLabelText('Roll number (USN)'), '1-2')
    await user.click(within(dialog).getByRole('button', { name: 'Add student' }))
    expect(await within(dialog).findByText(/USN must be 4 to 20/)).toBeInTheDocument()
  })

  describe('CSV import', () => {
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
      renderApp('/admin/upload/students')
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
      renderApp('/admin/upload/students')
      const form = await screen.findByRole('form', { name: 'Import the roster' })
      await user.upload(within(form).getByLabelText('Roster file'), csv)
      await user.click(within(form).getByRole('button', { name: 'Import roster' }))
      const alert = await within(form).findByRole('alert')
      expect(alert).toHaveTextContent(
        'Nothing was imported. Fix these 2 problems and upload again.',
      )
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
      renderApp('/admin/upload/students')
      const form = await screen.findByRole('form', { name: 'Import the roster' })
      await user.upload(within(form).getByLabelText('Roster file'), csv)
      await user.click(within(form).getByRole('button', { name: 'Import roster' }))
      expect(await within(form).findByRole('alert')).toHaveTextContent(
        'Send the roster as text/csv.',
      )
    })
  })
})
