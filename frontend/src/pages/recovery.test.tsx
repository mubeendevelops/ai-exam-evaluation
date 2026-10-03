import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { anonymous, renderApp } from '../test/render'
import { mockApi } from '../test/mockApi'
import { bloomOf } from '../test/fixtures'

afterEach(() => vi.unstubAllGlobals())

const bloom = async () => ({
  'GET /api/v1/auth/password-bloom': {
    body: await bloomOf(['password12345']),
    headers: { 'Content-Type': 'application/octet-stream' },
  },
})

describe('forgot password', () => {
  it('asks for a link and answers the same way whether or not the account exists', async () => {
    const mock = mockApi({
      ...anonymous,
      'POST /api/v1/auth/password/forgot': { status: 202, json: {} },
    })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await user.type(screen.getByLabelText(/Institution/), 'SYNTH_COLLEGE')
    await user.type(screen.getByLabelText(/Evaluator Email/), 'asha@college.test')
    await user.click(screen.getByRole('button', { name: /Email me a reset link/ }))

    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    expect(screen.getByText(/If an account exists/)).toBeInTheDocument()
    expect(screen.getByText(/expires in 30 minutes/)).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/auth/password/forgot')[0]?.body).toEqual({
      institution_id: 'SYNTH_COLLEGE',
      email: 'asha@college.test',
    })
  })

  it('links to the recovery-code screen and back to sign-in', async () => {
    mockApi(anonymous)
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await user.click(screen.getByRole('link', { name: 'Reset with a recovery code' }))
    expect(await screen.findByRole('heading', { name: 'Recover with a code' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: /Back to sign in/ }))
    expect(await screen.findByRole('heading', { name: /Customer Sign In/ })).toBeInTheDocument()
  })
})

describe('reset password', () => {
  it('sets a new password from the emailed link and takes the token out of the address', async () => {
    const mock = mockApi({
      ...anonymous,
      ...(await bloom()),
      'POST /api/v1/auth/password/reset': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/reset-password?token=college.secret-token')
    const first = await screen.findByLabelText('New Password')
    await user.type(first, 'a long uncommon passphrase')
    await user.type(screen.getByLabelText('Confirm Password'), 'a long uncommon passphrase')
    await user.click(screen.getByRole('button', { name: 'Update password' }))

    expect(await screen.findByRole('heading', { name: 'Password updated' })).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/auth/password/reset')[0]?.body).toEqual({
      token: 'college.secret-token',
      new_password: 'a long uncommon passphrase',
    })
  })

  it('does not send mismatching passwords', async () => {
    const mock = mockApi({ ...anonymous, ...(await bloom()) })
    const user = userEvent.setup()
    renderApp('/reset-password?token=t')
    await user.type(await screen.findByLabelText('New Password'), 'a long uncommon passphrase')
    await user.type(screen.getByLabelText('Confirm Password'), 'something else entirely')
    expect(screen.getByText('The two passwords do not match.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update password' })).toBeDisabled()
    expect(mock.callsTo('POST /api/v1/auth/password/reset')).toHaveLength(0)
  })

  it('explains an invalid, expired or used link, and policy failures', async () => {
    let n = 0
    mockApi({
      ...anonymous,
      ...(await bloom()),
      'POST /api/v1/auth/password/reset': () =>
        n++ === 0
          ? { status: 400, json: { detail: 'bad link' } }
          : {
              status: 422,
              json: {
                detail: 'Weak.',
                reasons: ['This password is too common or has appeared in a data breach.'],
              },
            },
    })
    const user = userEvent.setup()
    renderApp('/reset-password?token=t')
    await user.type(await screen.findByLabelText('New Password'), 'a long uncommon passphrase')
    await user.type(screen.getByLabelText('Confirm Password'), 'a long uncommon passphrase')
    await user.click(screen.getByRole('button', { name: 'Update password' }))
    expect(await screen.findByText(/invalid, has expired, or was already used/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Update password' }))
    expect(
      await screen.findByText(/too common or has appeared in a data breach/),
    ).toBeInTheDocument()
  })

  it('without a token it points to a new link', async () => {
    mockApi(anonymous)
    renderApp('/reset-password')
    expect(
      await screen.findByRole('heading', { name: 'Reset link incomplete' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Request a new link' })).toHaveAttribute(
      'href',
      '/forgot-password',
    )
  })
})

describe('recovery code', () => {
  it('resets the password with a one-time code, upper-casing what is typed', async () => {
    const mock = mockApi({
      ...anonymous,
      ...(await bloom()),
      'POST /api/v1/auth/password/recover': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/recover')
    await user.type(screen.getByLabelText(/Institution/), 'SYNTH_COLLEGE')
    await user.type(screen.getByLabelText(/Evaluator Email/), 'asha@college.test')
    await user.type(screen.getByLabelText('Recovery Code'), 'abcd-1234-abcd-1234')
    await user.type(screen.getByLabelText('New Password'), 'a long uncommon passphrase')
    await user.type(screen.getByLabelText('Confirm Password'), 'a long uncommon passphrase')
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    expect(await screen.findByRole('heading', { name: 'Password updated' })).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/auth/password/recover')[0]?.body).toEqual({
      institution_id: 'SYNTH_COLLEGE',
      email: 'asha@college.test',
      recovery_code: 'ABCD-1234-ABCD-1234',
      new_password: 'a long uncommon passphrase',
    })
  })

  it('gives one generic failure for any wrong detail', async () => {
    mockApi({
      ...anonymous,
      ...(await bloom()),
      'POST /api/v1/auth/password/recover': { status: 401, json: { detail: 'Recovery failed.' } },
    })
    const user = userEvent.setup()
    renderApp('/recover')
    await user.type(screen.getByLabelText(/Institution/), 'X')
    await user.type(screen.getByLabelText(/Evaluator Email/), 'a@b.test')
    await user.type(screen.getByLabelText('Recovery Code'), 'WRONG')
    await user.type(screen.getByLabelText('New Password'), 'a long uncommon passphrase')
    await user.type(screen.getByLabelText('Confirm Password'), 'a long uncommon passphrase')
    await user.click(screen.getByRole('button', { name: 'Reset password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Recovery failed.*lockout/)
  })
})

describe('verify email', () => {
  it('verifies once, even under React StrictMode, and tells a pending workspace to wait for approval', async () => {
    const mock = mockApi({
      ...anonymous,
      'POST /api/v1/registrations/verify-email': {
        json: { institution_id: 'NEW_COLLEGE', status: 'PENDING_APPROVAL' },
      },
    })
    const { render } = await import('@testing-library/react')
    const { MemoryRouter } = await import('react-router')
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query')
    const { AuthProvider } = await import('../auth/AuthProvider')
    const { default: App } = await import('../App')
    render(
      <StrictMode>
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={['/verify-email?token=verify-strict-1']}>
            <AuthProvider>
              <App />
            </AuthProvider>
          </MemoryRouter>
        </QueryClientProvider>
      </StrictMode>,
    )
    expect(await screen.findByRole('heading', { name: 'Email verified' })).toBeInTheDocument()
    expect(screen.getByText(/Tarn operator now reviews/)).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/registrations/verify-email')).toHaveLength(1)
    expect(mock.callsTo('POST /api/v1/registrations/verify-email')[0]?.body).toEqual({
      token: 'verify-strict-1',
    })
  })

  it('says the workspace is active when no approval is needed', async () => {
    mockApi({
      ...anonymous,
      'POST /api/v1/registrations/verify-email': {
        json: { institution_id: 'NEW_COLLEGE', status: 'ACTIVE' },
      },
    })
    renderApp('/verify-email?token=verify-active-1')
    expect(await screen.findByText(/The workspace is active/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Go to sign in/ })).toBeInTheDocument()
  })

  it('reports a bad or used link', async () => {
    mockApi({
      ...anonymous,
      'POST /api/v1/registrations/verify-email': { status: 400, json: { detail: 'bad' } },
    })
    renderApp('/verify-email?token=verify-bad-1')
    expect(await screen.findByRole('heading', { name: 'Email not verified' })).toBeInTheDocument()
    expect(screen.getByText(/invalid, has expired, or was already used/)).toBeInTheDocument()
  })

  it('without a token it asks for the email link', async () => {
    mockApi(anonymous)
    renderApp('/verify-email')
    expect(
      await screen.findByRole('heading', { name: 'Verification link incomplete' }),
    ).toBeInTheDocument()
  })
})

describe('accept invitation', () => {
  it('lets an invited teacher choose the first password', async () => {
    const mock = mockApi({
      ...anonymous,
      ...(await bloom()),
      'POST /api/v1/auth/invitations/accept': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/accept-invite?token=college.invite')
    await user.type(await screen.findByLabelText('Password'), 'a long uncommon passphrase')
    await user.type(screen.getByLabelText('Confirm Password'), 'a long uncommon passphrase')
    await user.click(screen.getByRole('button', { name: 'Create my account' }))
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Welcome aboard' })).toBeInTheDocument(),
    )
    expect(mock.callsTo('POST /api/v1/auth/invitations/accept')[0]?.body).toEqual({
      token: 'college.invite',
      password: 'a long uncommon passphrase',
    })
  })

  it('explains an expired invitation', async () => {
    mockApi({
      ...anonymous,
      ...(await bloom()),
      'POST /api/v1/auth/invitations/accept': { status: 400, json: { detail: 'bad' } },
    })
    const user = userEvent.setup()
    renderApp('/accept-invite?token=t')
    await user.type(await screen.findByLabelText('Password'), 'a long uncommon passphrase')
    await user.type(screen.getByLabelText('Confirm Password'), 'a long uncommon passphrase')
    await user.click(screen.getByRole('button', { name: 'Create my account' }))
    expect(await screen.findByText(/Ask your administrator to send a new one/)).toBeInTheDocument()
  })
})
