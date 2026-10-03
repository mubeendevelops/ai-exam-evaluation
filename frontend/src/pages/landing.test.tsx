import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { company, features, hero, placeholderTexts } from '../content/landing'
import { anonymous, renderApp } from '../test/render'
import { bloomOf, meOf, tokenOf } from '../test/fixtures'
import { mockApi } from '../test/mockApi'

afterEach(() => vi.unstubAllGlobals())

describe('landing page (MainLogin.html)', () => {
  it('shows branding, hero copy, value chips, sign-in card, feature cards and the footer from the config', async () => {
    mockApi(anonymous)
    renderApp('/')
    expect(screen.getAllByText('TARN KNOWLEDGE').length).toBeGreaterThan(0)
    expect(screen.getByText('Exam Evaluation Cloud')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      `${hero.titleLead} ${hero.titleAccent}`,
    )
    for (const chip of hero.chips) expect(screen.getByText(chip.text)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Customer Sign In/ })).toBeInTheDocument()
    for (const card of features)
      expect(screen.getByRole('heading', { name: card.title })).toBeInTheDocument()
    expect(screen.getByText(company.phone, { exact: false })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: company.email })).toHaveAttribute(
      'href',
      `mailto:${company.email}`,
    )
    expect(
      screen.getByText('Bangalore South, Bangalore – 560076,', { exact: false }),
    ).toBeInTheDocument()
    // The unverified figures are shown, but listed so they can be replaced.
    expect(placeholderTexts()).toEqual([
      'ISO Secure Cloud',
      '99.9% Evaluation Accuracy',
      expect.stringContaining('100,000+ pre-validated'),
    ])
    expect(await screen.findByText('Active Portal')).toBeInTheDocument()
  })

  it('shows "Portal unavailable" when the API does not answer', async () => {
    mockApi({ ...anonymous, 'GET /api/v1/health': { status: 503, json: { detail: 'down' } } })
    renderApp('/')
    expect(await screen.findByText('Portal unavailable')).toBeInTheDocument()
  })

  it('opens each demo preview in a dialog and closes it again', async () => {
    mockApi(anonymous)
    const user = userEvent.setup()
    renderApp('/')

    await user.click(screen.getByRole('button', { name: /Get Cloud Question Bank/ }))
    let dialog = screen.getByRole('dialog', { name: 'Explore Repository Schema' })
    expect(within(dialog).getByText('cognitive_complexity_score')).toBeInTheDocument()
    expect(within(dialog).getByText('Static preview with sample data')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Close Preview' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    await user.click(screen.getByRole('button', { name: /Cloud Map Answer Keys/ }))
    dialog = screen.getByRole('dialog', { name: /Answer Key Mapping/ })
    expect(within(dialog).getByText(/AI Search & Auto-Synthesize/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Direct Upload' }))
    expect(within(dialog).getByText(/Drag & Drop official answer key/)).toBeInTheDocument()
    await user.keyboard('{Escape}')

    await user.click(screen.getByRole('button', { name: /Intelligent Assessment Assistant/ }))
    dialog = screen.getByRole('dialog', { name: /Tri-Window/ })
    expect(within(dialog).getByText('3. AI Evaluation & Override')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /Confirm & Commit Score/ })).toBeDisabled()
    await user.keyboard('{Escape}')
  })

  it('marks the analytics card and its preview as "preview only"', async () => {
    mockApi(anonymous)
    const user = userEvent.setup()
    renderApp('/')
    const card = screen.getByRole('button', { name: /Cloud Analytics On House/ })
    expect(within(card).getByText('Preview only')).toBeInTheDocument()
    await user.click(card)
    const dialog = screen.getByRole('dialog', { name: /Performance Analytics/ })
    expect(within(dialog).getByText(/Analytics is not part of the product yet/)).toBeInTheDocument()
  })

  it('shows the legal links as plain text: those pages do not exist yet', () => {
    mockApi(anonymous)
    renderApp('/')
    for (const name of company.legal) {
      expect(screen.getByText(name)).toBeInTheDocument()
      expect(screen.queryByRole('link', { name })).toBeNull()
    }
  })
})

describe('sign-in', () => {
  async function fill(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText(/Institution \/ Org Domain ID/), 'synth_college')
    await user.type(screen.getByLabelText(/Evaluator Email/), 'asha@college.test')
    await user.type(screen.getByLabelText(/Security Access Password/), 'correct horse battery')
  }

  it('signs in with the typed values and lands on the Q&A tab', async () => {
    const mock = mockApi({
      ...anonymous,
      'POST /api/v1/auth/login': { json: tokenOf() },
      'GET /api/v1/auth/me': { json: meOf() },
    })
    const user = userEvent.setup()
    renderApp('/')
    await fill(user)
    await user.click(screen.getByLabelText('Remember session'))
    await user.click(screen.getByRole('button', { name: /Authenticate Cloud Access/ }))

    expect(
      await screen.findByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/auth/login')[0]?.body).toEqual({
      institution_id: 'synth_college',
      email: 'asha@college.test',
      password: 'correct horse battery',
      remember: true,
    })
    // The access token is held in memory and sent on the next call.
    expect(mock.callsTo('GET /api/v1/auth/me').at(-1)?.headers.get('Authorization')).toBe(
      'Bearer access-1',
    )
  })

  it('shows the generic failure, keeps the other fields and clears the password', async () => {
    mockApi({
      ...anonymous,
      'POST /api/v1/auth/login': {
        status: 401,
        json: { detail: 'Sign-in failed. Check everything.' },
      },
    })
    const user = userEvent.setup()
    renderApp('/')
    await fill(user)
    await user.click(screen.getByRole('button', { name: /Authenticate Cloud Access/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign-in failed. Check everything.')
    expect(screen.getByLabelText(/Evaluator Email/)).toHaveValue('asha@college.test')
    expect(screen.getByLabelText(/Security Access Password/)).toHaveValue('')
  })

  it('says so when the server cannot be reached', async () => {
    mockApi({
      ...anonymous,
      'POST /api/v1/auth/login': () => Promise.reject(new TypeError('network')),
    })
    const user = userEvent.setup()
    renderApp('/')
    await fill(user)
    await user.click(screen.getByRole('button', { name: /Authenticate Cloud Access/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Cannot reach the server/)
  })

  it('sends a user whose administrator forced a reset to the new-password screen', async () => {
    mockApi({
      ...anonymous,
      'POST /api/v1/auth/login': {
        json: { status: 'reset_required', reset_token: 'college.reset-secret' },
      },
    })
    const user = userEvent.setup()
    renderApp('/')
    await fill(user)
    await user.click(screen.getByRole('button', { name: /Authenticate Cloud Access/ }))
    expect(await screen.findByRole('heading', { name: 'Set a new password' })).toBeInTheDocument()
    expect(screen.getByText(/administrator requires a new password/)).toBeInTheDocument()
  })

  it('"Forgot Password?" opens the forgot-password screen', async () => {
    mockApi(anonymous)
    const user = userEvent.setup()
    renderApp('/')
    await user.click(screen.getByRole('link', { name: 'Forgot Password?' }))
    expect(
      await screen.findByRole('heading', { name: 'Forgot your password?' }),
    ).toBeInTheDocument()
  })

  it('a returning visitor with a refresh cookie goes straight to the app', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': { json: tokenOf() },
      'GET /api/v1/auth/me': { json: meOf() },
      'GET /api/v1/health': { json: (await import('../test/fixtures')).healthy },
    })
    renderApp('/')
    expect(
      await screen.findByRole('heading', { name: 'Questions & Answers Repository' }),
    ).toBeInTheDocument()
  })
})

describe('registration modal', () => {
  const routes = async () => ({
    ...anonymous,
    'GET /api/v1/auth/password-bloom': {
      body: await bloomOf(['password12345']),
      headers: { 'Content-Type': 'application/octet-stream' },
    },
  })

  async function open(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: /Register/ }))
    return screen.getByRole('dialog', { name: 'Create Cloud Workspace' })
  }

  it('validates the Institution ID format as you type', async () => {
    mockApi(await routes())
    const user = userEvent.setup()
    renderApp('/')
    const dialog = await open(user)
    const field = within(dialog).getByLabelText(/Institution ID \/ Org Domain ID/)
    await user.type(field, 'bad id')
    expect(within(dialog).getByText(/Invalid Format: Upper case/)).toBeInTheDocument()
    await user.clear(field)
    await user.type(field, 'TARN_01')
    expect(within(dialog).getByText('Format matches specification.')).toBeInTheDocument()
  })

  it('checks availability: available, taken, and never asks the server about a malformed ID', async () => {
    const mock = mockApi({
      ...(await routes()),
      'GET /api/v1/registrations/availability': (call) => {
        const id = call.query.get('institution_id') ?? ''
        return { json: { institution_id: id, valid: true, available: id !== 'TAKEN' } }
      },
    })
    const user = userEvent.setup()
    renderApp('/')
    const dialog = await open(user)
    const field = within(dialog).getByLabelText(/Institution ID \/ Org Domain ID/)
    const check = within(dialog).getByRole('button', { name: 'Check' })

    await user.type(field, 'bad id')
    await user.click(check)
    expect(mock.callsTo('GET /api/v1/registrations/availability')).toHaveLength(0)

    await user.clear(field)
    await user.type(field, 'FREE_ONE')
    await user.click(check)
    expect(await within(dialog).findByText('Org ID "FREE_ONE" is AVAILABLE!')).toBeInTheDocument()

    await user.clear(field)
    await user.type(field, 'TAKEN')
    await user.click(check)
    expect(await within(dialog).findByText(/Org ID "TAKEN" is ALREADY TAKEN/)).toBeInTheDocument()
  })

  it('shows the password strength hint: short, common (from the Bloom filter), acceptable, high', async () => {
    mockApi(await routes())
    const user = userEvent.setup()
    renderApp('/')
    const dialog = await open(user)
    const password = within(dialog).getByLabelText(/Security Password/)
    const status = () => within(dialog).getByTestId('strength-status')
    expect(status()).toHaveTextContent('Idle')

    await user.type(password, 'short')
    expect(status()).toHaveTextContent('TOO SHORT')
    await user.clear(password)
    await user.type(password, 'password12345')
    await waitFor(() => expect(status()).toHaveTextContent('FLAGGED / WEAK'))
    await user.clear(password)
    await user.type(password, 'an uncommon one')
    await waitFor(() => expect(status()).toHaveTextContent('ACCEPTABLE'))
    await user.type(password, ' with more words')
    await waitFor(() => expect(status()).toHaveTextContent('HIGH SECURE'))
    expect(within(dialog).getByRole('meter')).toHaveAttribute('aria-valuenow', '100')
  })

  it('still works when the Bloom filter cannot be downloaded: only the length is judged', async () => {
    mockApi({ ...anonymous, 'GET /api/v1/auth/password-bloom': { status: 500, json: {} } })
    const user = userEvent.setup()
    renderApp('/')
    const dialog = await open(user)
    await user.type(within(dialog).getByLabelText(/Security Password/), 'password12345')
    await waitFor(() =>
      expect(within(dialog).getByTestId('strength-status')).toHaveTextContent('ACCEPTABLE'),
    )
  })

  async function fillAll(dialog: HTMLElement, user: ReturnType<typeof userEvent.setup>) {
    await user.type(within(dialog).getByLabelText(/Institution ID \/ Org Domain ID/), 'NEW_COLLEGE')
    await user.type(within(dialog).getByLabelText(/College \/ Institution Name/), 'New College')
    await user.type(within(dialog).getByLabelText(/Admin Full Name/), 'Dr. Admin')
    await user.type(within(dialog).getByLabelText(/Official Email/), 'admin@new.test')
    await user.type(
      within(dialog).getByLabelText(/Security Password/),
      'a long uncommon passphrase',
    )
  }

  it('registers, then tells the admin to verify the email and wait for approval', async () => {
    const mock = mockApi({
      ...(await routes()),
      'POST /api/v1/registrations': {
        status: 202,
        json: {
          institution_id: 'NEW_COLLEGE',
          status: 'PENDING_VERIFICATION',
          approval_required: true,
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/')
    const dialog = await open(user)
    await fillAll(dialog, user)
    await user.click(within(dialog).getByRole('button', { name: /Complete Registration/ }))

    expect(await screen.findByText('Check your inbox to verify your email')).toBeInTheDocument()
    expect(screen.getByText(/a Tarn operator reviews the registration/)).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/registrations')[0]?.body).toEqual({
      institution_id: 'NEW_COLLEGE',
      institution_name: 'New College',
      admin_name: 'Dr. Admin',
      email: 'admin@new.test',
      password: 'a long uncommon passphrase',
    })
  })

  it('reports a taken ID and password policy reasons from the server', async () => {
    let n = 0
    mockApi({
      ...(await routes()),
      'POST /api/v1/registrations': () =>
        n++ === 0
          ? { status: 409, json: { detail: 'That Institution ID is taken.' } }
          : {
              status: 422,
              json: { detail: 'Password too weak.', reasons: ['Use at least 12 characters.'] },
            },
    })
    const user = userEvent.setup()
    renderApp('/')
    const dialog = await open(user)
    await fillAll(dialog, user)
    await user.click(within(dialog).getByRole('button', { name: /Complete Registration/ }))
    expect(await within(dialog).findByText('That Institution ID is taken.')).toBeInTheDocument()
    expect(within(dialog).getByText(/ALREADY TAKEN/)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: /Complete Registration/ }))
    expect(await within(dialog).findByText('Use at least 12 characters.')).toBeInTheDocument()
  })

  it('does not submit a malformed Institution ID', async () => {
    const mock = mockApi(await routes())
    const user = userEvent.setup()
    renderApp('/')
    const dialog = await open(user)
    await fillAll(dialog, user)
    const id = within(dialog).getByLabelText(/Institution ID \/ Org Domain ID/)
    await user.clear(id)
    await user.type(id, 'lower')
    await user.click(within(dialog).getByRole('button', { name: /Complete Registration/ }))
    expect(await within(dialog).findByText(/correct the Institution ID errors/)).toBeInTheDocument()
    expect(mock.callsTo('POST /api/v1/registrations')).toHaveLength(0)
  })
})
