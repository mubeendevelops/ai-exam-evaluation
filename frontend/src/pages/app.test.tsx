import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { healthy, meOf, tokenOf } from '../test/fixtures'
import { mockApi } from '../test/mockApi'
import { renderApp } from '../test/render'
import { initialsOf } from '../components/app/UserMenu'

afterEach(() => vi.unstubAllGlobals())

/** A signed-in browser: the refresh cookie works and /auth/me answers. */
function signedIn(
  role: 'teacher' | 'admin' = 'teacher',
  extra: Parameters<typeof mockApi>[0] = {},
  left = 10,
) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf({ role }) },
    'GET /api/v1/auth/me': { json: meOf({ role }, left) },
    'GET /api/v1/health': { json: healthy },
    ...extra,
  })
}

describe('route guards', () => {
  it('sends a visitor who is not signed in from an app page to the landing page', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': { status: 401, json: { detail: 'Sign in again.' } },
      'GET /api/v1/health': { json: healthy },
    })
    renderApp('/evaluate')
    expect(await screen.findByRole('heading', { name: /Customer Sign In/ })).toBeInTheDocument()
  })

  it('keeps /admin for administrators: a teacher is sent to the Q&A tab', async () => {
    signedIn('teacher')
    renderApp('/admin')
    expect(
      await screen.findByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeInTheDocument()
  })

  it('unknown addresses go to the start page', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': { status: 401, json: {} },
      'GET /api/v1/health': { json: healthy },
    })
    renderApp('/nowhere')
    expect(await screen.findByRole('heading', { name: /Customer Sign In/ })).toBeInTheDocument()
  })
})

describe('app shell (project_idea.html)', () => {
  it('has the tabs, the mobile bar and the footer; tabs switch the page', async () => {
    signedIn()
    const user = userEvent.setup()
    renderApp('/qna')
    expect(
      await screen.findByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeInTheDocument()

    const main = screen.getByRole('navigation', { name: 'Main' })
    expect(
      within(main)
        .getAllByRole('link')
        .map((a) => a.textContent),
    ).toEqual(['Q&A DB', 'AI Evaluation', 'Evaluated', 'Schema Designer'])
    const mobile = screen.getByRole('navigation', { name: 'Main (mobile)' })
    expect(
      within(mobile)
        .getAllByRole('link')
        .map((a) => a.textContent),
    ).toEqual(['Q&A DB', 'AI Eval', 'Evaluated', 'Schema'])
    expect(screen.getByText('TARN KNOWLEDGE SERVICES')).toBeInTheDocument()

    await user.click(within(main).getByRole('link', { name: 'AI Evaluation' }))
    expect(
      await screen.findByRole('heading', { name: 'AI Automated Answer Evaluation Pipeline' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '1. Scan Upload' })).toHaveAttribute(
      'aria-current',
      'step',
    )
    expect(within(main).getByRole('link', { name: 'AI Evaluation' })).toHaveAttribute(
      'aria-current',
      'page',
    )

    await user.click(within(mobile).getByRole('link', { name: 'Schema' }))
    expect(
      await screen.findByRole('heading', { name: 'Question Paper Studio & Schema Engine' }),
    ).toBeInTheDocument()
  })

  it("replaces the TK avatar with the signed-in user's initials and menu", async () => {
    signedIn()
    const user = userEvent.setup()
    renderApp('/qna')
    const avatar = await screen.findByRole('button', { name: /Account menu for Asha Rao/ })
    expect(avatar).toHaveTextContent('AR')
    expect(screen.queryByText('TK')).toBeNull()
    await user.click(avatar)
    const menu = screen.getByRole('menu')
    expect(within(menu).getByText('asha@college.test')).toBeInTheDocument()
    expect(within(menu).getByText(/Synthetic College/)).toBeInTheDocument()
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((i) => i.textContent?.trim()),
    ).toEqual(['Profile', 'Recovery codes', 'Sign out'])
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('adds Administration to the menu of an administrator', async () => {
    signedIn('admin')
    const user = userEvent.setup()
    renderApp('/qna')
    await user.click(await screen.findByRole('button', { name: /Account menu/ }))
    await user.click(screen.getByRole('menuitem', { name: /Administration/ }))
    expect(await screen.findByRole('heading', { name: 'Administration' })).toBeInTheDocument()
  })

  it('signs out: tells the server, forgets the token and returns to the landing page', async () => {
    const mock = signedIn('teacher', { 'POST /api/v1/auth/logout': { status: 204 } })
    const user = userEvent.setup()
    renderApp('/qna')
    await user.click(await screen.findByRole('button', { name: /Account menu/ }))
    await user.click(screen.getByRole('menuitem', { name: /Sign out/ }))
    expect(await screen.findByRole('heading', { name: /Customer Sign In/ })).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/auth/logout')).toHaveLength(1)
    expect(mock.callsTo('POST /api/v1/auth/logout')[0]?.headers.get('Authorization')).toBe(
      'Bearer access-1',
    )
  })

  it('signs out: forgets every cached answer of the server (the next user may be another college)', async () => {
    signedIn('teacher', { 'POST /api/v1/auth/logout': { status: 204 } })
    const user = userEvent.setup()
    const { queryClient } = renderApp('/qna')
    await user.click(await screen.findByRole('button', { name: /Account menu/ }))
    await waitFor(() => expect(queryClient.getQueryCache().getAll().length).toBeGreaterThan(0))
    await user.click(screen.getByRole('menuitem', { name: /Sign out/ }))
    expect(await screen.findByRole('heading', { name: /Customer Sign In/ })).toBeInTheDocument()
    expect(
      queryClient
        .getQueryCache()
        .getAll()
        .filter((q) => q.state.data !== undefined && q.queryKey[0] !== 'health'),
    ).toEqual([])
  })

  it('computes initials from the first and last name', () => {
    expect(initialsOf('Asha Rao')).toBe('AR')
    expect(initialsOf('Dr. Sarah J. Connor')).toBe('DC')
    expect(initialsOf('madonna')).toBe('M')
    expect(initialsOf('   ')).toBe('?')
  })
})

describe('status pill', () => {
  async function pillFor(health: unknown, status = 200) {
    signedIn('teacher', { 'GET /api/v1/health': { status, json: health } })
    renderApp('/qna')
    await screen.findByRole('heading', { name: 'Questions & Answers Repository' })
  }
  const pill = () =>
    screen
      .getAllByRole('status')
      .find(
        (el) =>
          el.getAttribute('title') !== null || /online|offline|Checking/.test(el.textContent ?? ''),
      )!

  it('shows real API and worker health', async () => {
    await pillFor(healthy)
    await waitFor(() => expect(pill()).toHaveTextContent('API & worker online'))
    expect(pill()).toHaveAttribute('title', expect.stringContaining('Worker running'))
  })

  it('shows a worker that is down', async () => {
    await pillFor({ ...healthy, worker: { status: 'down', detail: 'Worker not reachable' } })
    await waitFor(() => expect(pill()).toHaveTextContent('Worker offline'))
  })

  it('shows an unconfigured worker as just "API online"', async () => {
    await pillFor({
      ...healthy,
      worker: { status: 'unknown', detail: 'Worker address not configured' },
    })
    await waitFor(() => expect(pill()).toHaveTextContent('API online'))
  })

  it('shows an API that does not answer', async () => {
    await pillFor({ detail: 'down' }, 503)
    await waitFor(() => expect(pill()).toHaveTextContent('API offline'))
  })
})

describe('profile and recovery codes', () => {
  async function openMenuItem(name: RegExp) {
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Account menu/ }))
    await user.click(screen.getByRole('menuitem', { name }))
    return user
  }

  it('shows the profile and changes the password', async () => {
    const mock = signedIn('teacher', {
      'POST /api/v1/auth/password/change': { status: 204 },
      'GET /api/v1/auth/password-bloom': { body: new ArrayBuffer(0), status: 500 },
    })
    renderApp('/qna')
    const user = await openMenuItem(/Profile/)
    const dialog = screen.getByRole('dialog', { name: 'Profile' })
    expect(within(dialog).getByText('SYNTH_COLLEGE')).toBeInTheDocument()
    expect(within(dialog).getByText('teacher')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Current Password'), 'old password here')
    await user.type(within(dialog).getByLabelText('New Password'), 'a long uncommon passphrase')
    await user.click(within(dialog).getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Password changed.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mock.callsTo('POST /api/v1/auth/password/change')[0]?.body).toEqual({
      current_password: 'old password here',
      new_password: 'a long uncommon passphrase',
    })
  })

  it('refuses a wrong current password', async () => {
    signedIn('teacher', {
      'POST /api/v1/auth/password/change': { status: 403, json: { detail: 'no' } },
      'GET /api/v1/auth/password-bloom': { status: 500, json: {} },
    })
    renderApp('/qna')
    const user = await openMenuItem(/Profile/)
    const dialog = screen.getByRole('dialog', { name: 'Profile' })
    await user.type(within(dialog).getByLabelText('Current Password'), 'wrong')
    await user.type(within(dialog).getByLabelText('New Password'), 'a long uncommon passphrase')
    await user.click(within(dialog).getByRole('button', { name: 'Change password' }))
    expect(
      await within(dialog).findByText('The current password is not correct.'),
    ).toBeInTheDocument()
  })

  it('creates recovery codes, shows them once and forgets them when closed', async () => {
    const codes = Array.from({ length: 10 }, (_, i) => `ABCD-EFGH-IJKL-${String(1000 + i)}`)
    const mock = signedIn(
      'teacher',
      { 'POST /api/v1/auth/recovery-codes': { status: 201, json: { codes } } },
      3,
    )
    renderApp('/qna')
    const user = await openMenuItem(/Recovery codes/)
    const dialog = screen.getByRole('dialog', { name: 'Recovery codes' })
    expect(within(dialog).getByText('3 unused codes')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Current Password'), 'current password')
    await user.click(within(dialog).getByRole('button', { name: 'Create new recovery codes' }))

    const list = await within(dialog).findByRole('list', { name: 'Recovery codes' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(10)
    expect(within(dialog).getByText('once')).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/auth/recovery-codes')[0]?.body).toEqual({
      current_password: 'current password',
    })
    // The count is reloaded from the server after issuing.
    await waitFor(() => expect(mock.callsTo('GET /api/v1/auth/me').length).toBeGreaterThan(1))

    const done = within(dialog).getByRole('button', { name: 'Done' })
    expect(done).toBeDisabled()
    await user.click(within(dialog).getByLabelText('I have saved these codes'))
    await user.click(done)
    expect(screen.queryByRole('dialog')).toBeNull()

    // Opening it again does not bring the codes back.
    await user.click(screen.getByRole('button', { name: /Account menu/ }))
    await user.click(screen.getByRole('menuitem', { name: /Recovery codes/ }))
    expect(screen.queryByText(codes[0]!)).toBeNull()
  })

  it('needs the current password to be right', async () => {
    signedIn('teacher', {
      'POST /api/v1/auth/recovery-codes': { status: 403, json: { detail: 'no' } },
    })
    renderApp('/qna')
    const user = await openMenuItem(/Recovery codes/)
    const dialog = screen.getByRole('dialog', { name: 'Recovery codes' })
    await user.type(within(dialog).getByLabelText('Current Password'), 'wrong')
    await user.click(within(dialog).getByRole('button', { name: 'Create new recovery codes' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'The current password is not correct.',
    )
  })
})
