import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { unitOf } from '../test/adminFixtures'
import { healthy, meOf, tokenOf } from '../test/fixtures'
import { mockApi } from '../test/mockApi'
import { renderApp } from '../test/render'

afterEach(() => vi.unstubAllGlobals())

const submission = {
  id: 'sub-1',
  submitted_by: { id: 't-1', name: 'Asha Rao' },
  submitted_at: '2026-10-28T10:00:00Z',
  unmarked: [{ booklet_id: 'bk-2', reason: 'absent' as const, note: '' }],
  returned_at: null,
  returned_by: null,
  return_note: '',
}

function governor(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf({ role: 'governor' }) },
    'GET /api/v1/auth/me': { json: meOf({ role: 'governor' }) },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/units': {
      json: [
        unitOf({ state: 'submitted', version: 3, submission }),
        unitOf({
          id: 'unit-2',
          code: 'U-0002',
          state: 'frozen',
          submission,
          frozen: { frozen_by: { id: 'g-1', name: 'Governor' }, frozen_at: '2026-10-27T10:00:00Z' },
        }),
      ],
    },
    ...extra,
  })
}

describe('governor: freeze and sign off (P28)', () => {
  it('lists the submitted units and freezes one after asking', async () => {
    const mock = governor({
      'POST /api/v1/units/unit-1/freeze': { json: unitOf({ state: 'frozen' }) },
    })
    const user = userEvent.setup()
    renderApp('/governor')
    const waiting = await screen.findByRole('region', { name: 'Submitted, waiting for sign-off' })
    expect(waiting).toHaveTextContent('U-0001')
    expect(waiting).toHaveTextContent('1 not marked (with a reason)')
    expect(screen.getByRole('region', { name: 'Frozen and signed off' })).toHaveTextContent(
      'U-0002',
    )
    await user.click(within(waiting).getByRole('button', { name: /Freeze and sign off/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('This is final')
    await user.click(within(dialog).getByRole('button', { name: 'Freeze and sign off' }))
    await waitFor(() => expect(mock.callsTo('POST /api/v1/units/unit-1/freeze')).toHaveLength(1))
    expect(mock.callsTo('POST /api/v1/units/unit-1/freeze')[0]?.body).toEqual({
      expected_version: 3,
    })
  })
})
