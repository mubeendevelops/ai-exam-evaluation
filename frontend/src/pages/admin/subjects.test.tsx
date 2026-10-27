import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { adminSession, chemistry, physics } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'

afterEach(() => vi.unstubAllGlobals())

async function rowOf(name: string) {
  const heading = await screen.findAllByRole('rowheader', { name })
  const row = heading.map((h) => h.closest('tr')).find((r) => r?.querySelector('.font-mono'))
  if (!row) throw new Error('no subject row')
  return within(row)
}

describe('subjects', () => {
  it('lists every subject with its status, retired ones without actions', async () => {
    adminSession()
    renderApp('/admin/smes')
    const phy = await rowOf('Physics')
    expect(phy.getByText('PHY')).toBeInTheDocument()
    expect(phy.getByText('Active')).toBeInTheDocument()
    expect(phy.getByRole('button', { name: 'Rename' })).toBeInTheDocument()
    const che = await rowOf('Chemistry')
    expect(che.getByText('Retired')).toBeInTheDocument()
    expect(che.queryByRole('button')).toBeNull()
  })

  it('adds a subject', async () => {
    const mock = adminSession({
      'POST /api/v1/subjects': { status: 201, json: { ...physics, id: 'sub-9' } },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click(await screen.findByRole('button', { name: 'Add subject' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add subject' })
    await user.type(within(dialog).getByLabelText('Code'), ' BIO-101 ')
    await user.type(within(dialog).getByLabelText('Subject name'), 'Biology')
    await user.click(within(dialog).getByRole('button', { name: 'Add subject' }))
    expect(await screen.findByText('Subject Biology added.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/subjects')[0]?.body).toEqual({
      code: 'BIO-101',
      name: 'Biology',
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows a duplicate code and keeps the dialog', async () => {
    adminSession({
      'POST /api/v1/subjects': { status: 409, json: { detail: 'That subject code is taken.' } },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click(await screen.findByRole('button', { name: 'Add subject' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add subject' })
    await user.type(within(dialog).getByLabelText('Code'), 'PHY')
    await user.type(within(dialog).getByLabelText('Subject name'), 'Again')
    await user.click(within(dialog).getByRole('button', { name: 'Add subject' }))
    expect(await within(dialog).findByText('That subject code is taken.')).toBeInTheDocument()
  })

  it('renames a subject, starting from its current code and name', async () => {
    const mock = adminSession({
      'PUT /api/v1/subjects/sub-1': { json: { ...physics, name: 'Applied Physics', version: 2 } },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Physics')).getByRole('button', { name: 'Rename' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rename subject' })
    expect(within(dialog).getByLabelText('Code')).toHaveValue('PHY')
    const name = within(dialog).getByLabelText('Subject name')
    await user.clear(name)
    await user.type(name, 'Applied Physics')
    await user.click(within(dialog).getByRole('button', { name: 'Save subject' }))
    expect(await screen.findByText('Subject renamed.')).toBeInTheDocument()
    expect(mock.callsTo('PUT /api/v1/subjects/sub-1')[0]?.body).toEqual({
      code: 'PHY',
      name: 'Applied Physics',
    })
  })

  it('asks before retiring, then retires', async () => {
    const mock = adminSession({
      'POST /api/v1/subjects/sub-1/retire': { json: { ...physics, retired: true, version: 2 } },
    })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    await user.click((await rowOf('Physics')).getByRole('button', { name: 'Retire' }))
    const dialog = screen.getByRole('dialog', { name: 'Retire this subject?' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(mock.callsTo('POST /api/v1/subjects/sub-1/retire')).toHaveLength(0)

    await user.click((await rowOf('Physics')).getByRole('button', { name: 'Retire' }))
    await user.click(screen.getByRole('button', { name: 'Retire subject' }))
    expect(await screen.findByText('Subject Physics retired.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/subjects/sub-1/retire')).toHaveLength(1)
    expect(chemistry.retired).toBe(true)
  })

  it('says so when the college has no subjects, and offers none when adding an SME', async () => {
    adminSession({ 'GET /api/v1/subjects': { json: [] } })
    const user = userEvent.setup()
    renderApp('/admin/smes')
    expect(await screen.findByText('No subjects yet. Add the first one.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add SME' }))
    expect(
      within(await screen.findByRole('dialog', { name: 'Add SME' })).getByText(
        'No subjects yet. Add a subject first.',
      ),
    ).toBeInTheDocument()
  })
})
