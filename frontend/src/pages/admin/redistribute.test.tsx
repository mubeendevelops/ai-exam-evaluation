import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  COUNTS,
  adminSession,
  asha,
  kiran,
  physics,
  requestOf,
  unitOf,
} from '../../test/adminFixtures'
import { renderApp } from '../../test/render'

afterEach(() => vi.unstubAllGlobals())

const UNIT = unitOf({ counts: { ...COUNTS, total: 4, completed: 1 } })
const KIRANS = unitOf({
  id: 'unit-2',
  code: 'U-0002',
  owner: { id: kiran.id, name: kiran.display_name },
})
const SMES = [
  { id: asha.id, name: asha.display_name, email: asha.email },
  { id: kiran.id, name: kiran.display_name, email: kiran.email },
]

function session(extra: Parameters<typeof adminSession>[0] = {}) {
  return adminSession({
    'GET /api/v1/units': { json: [UNIT, KIRANS] },
    'GET /api/v1/units/smes': { json: SMES },
    ...extra,
  })
}

describe('Redistribute Workload', () => {
  it('lists open units by SME and moves one to an entitled SME with a reason', async () => {
    const mock = session({
      'POST /api/v1/units/unit-1/transfer': {
        json: { ...UNIT, owner: { id: kiran.id, name: kiran.display_name }, version: 2 },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/redistribute')
    const ashas = await screen.findByRole('region', { name: 'Units of Asha Rao' })
    expect(within(ashas).getByText('1 of 4 completed')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Units of Kiran Shah' })).toBeInTheDocument()
    expect(mock.callsTo('GET /api/v1/units')[0]?.query.get('state')).toBe('open')

    await user.click(within(ashas).getByRole('button', { name: 'Transfer' }))
    const dialog = await screen.findByRole('dialog', { name: 'Transfer U-0001' })
    const target = within(dialog).getByLabelText('New SME')
    // The current owner is not offered; the list is the subject's entitled SMEs.
    expect(within(target).queryByRole('option', { name: /Asha Rao/ })).toBeNull()
    expect(mock.callsTo('GET /api/v1/units/smes')[0]?.query.get('subject_id')).toBe(physics.id)
    const submit = within(dialog).getByRole('button', { name: 'Transfer unit' })
    expect(submit).toBeDisabled()
    await user.selectOptions(target, kiran.id)
    expect(submit).toBeDisabled() // the reason is required
    await user.type(within(dialog).getByLabelText('Reason for the transfer'), 'Asha on leave')
    await user.click(submit)
    expect(await screen.findByText(/U-0001 moved to Kiran Shah/)).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/units/unit-1/transfer')[0]?.body).toEqual({
      owner_id: kiran.id,
      reason: 'Asha on leave',
      expected_version: 1,
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('keeps the dialog open with the reason when the transfer is refused', async () => {
    session({
      'POST /api/v1/units/unit-1/transfer': {
        status: 409,
        json: { detail: 'The unit changed since it was loaded: reload it.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/redistribute')
    const ashas = await screen.findByRole('region', { name: 'Units of Asha Rao' })
    await user.click(within(ashas).getByRole('button', { name: 'Transfer' }))
    const dialog = await screen.findByRole('dialog', { name: 'Transfer U-0001' })
    await user.selectOptions(within(dialog).getByLabelText('New SME'), kiran.id)
    await user.type(within(dialog).getByLabelText('Reason for the transfer'), 'x')
    await user.click(within(dialog).getByRole('button', { name: 'Transfer unit' }))
    expect(await within(dialog).findByText(/changed since it was loaded/)).toBeInTheDocument()
  })

  it('answers the inbox: transfer prefilled with the request, or decline with a note', async () => {
    const mock = session({
      'GET /api/v1/delegation-requests': { json: [requestOf()] },
      'POST /api/v1/units/unit-1/transfer': { json: UNIT },
      'POST /api/v1/delegation-requests/req-1/decline': {
        json: requestOf({ state: 'declined', note: 'Nobody free' }),
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/redistribute')
    const request = await screen.findByRole('listitem', { name: 'Request for U-0001' })
    expect(within(request).getByText('On leave next week')).toBeInTheDocument()
    expect(within(request).getByText('Pending')).toBeInTheDocument()
    expect(mock.callsTo('GET /api/v1/delegation-requests')[0]?.query.get('state')).toBe('pending')

    await user.click(within(request).getByRole('button', { name: 'Transfer' }))
    const dialog = await screen.findByRole('dialog', { name: 'Transfer U-0001' })
    expect(within(dialog).getByLabelText('Reason for the transfer')).toHaveValue(
      'On leave next week',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await user.click(within(request).getByRole('button', { name: 'Decline' }))
    const decline = await screen.findByRole('dialog', { name: 'Decline the request for U-0001' })
    await user.type(within(decline).getByLabelText('Note to the SME'), 'Nobody free')
    await user.click(within(decline).getByRole('button', { name: 'Decline request' }))
    expect(await screen.findByText('Request for U-0001 declined.')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/delegation-requests/req-1/decline')[0]?.body).toEqual({
      note: 'Nobody free',
    })
  })

  it('filters the inbox by state', async () => {
    const mock = session({
      'GET /api/v1/delegation-requests': (call) => ({
        json:
          call.query.get('state') === 'declined'
            ? [requestOf({ state: 'declined', note: 'No', closed_at: '2026-10-28T11:00:00Z' })]
            : [],
      }),
    })
    const user = userEvent.setup()
    renderApp('/admin/redistribute')
    expect(await screen.findByText('No request is waiting.')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Show'), 'declined')
    const request = await screen.findByRole('listitem', { name: 'Request for U-0001' })
    expect(within(request).getByText('Declined')).toBeInTheDocument()
    expect(within(request).queryByRole('button', { name: 'Decline' })).toBeNull()
    expect(mock.callsTo('GET /api/v1/delegation-requests').at(-1)?.query.get('state')).toBe(
      'declined',
    )
  })

  it('says so when there is nothing open', async () => {
    adminSession()
    renderApp('/admin/redistribute')
    expect(await screen.findByRole('heading', { name: 'No open units' })).toBeInTheDocument()
  })
})
