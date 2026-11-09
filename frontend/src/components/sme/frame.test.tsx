import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { adminSession } from '../../test/adminFixtures'
import { renderApp } from '../../test/render'
import { smeSession } from '../../test/smeFixtures'

afterEach(() => vi.unstubAllGlobals())

const GROUPS: [string, string[]][] = [
  ['Workspace', ['My Assignments', 'Manage Question Papers', 'Manage Schema']],
  ['Account & Insights', ['My Profile', 'My Reports']],
  ['Older screens', ['Q&A DB', 'Schema Designer', 'Evaluated', 'AI Evaluation']],
]

function linksOf(group: HTMLElement): string[] {
  return within(group)
    .getAllByRole('link')
    .map((l) => l.textContent?.replace(/\d+ Active$/, '').trim() ?? '')
}

describe('SME portal frame (P31, D215)', () => {
  it('lands on My Assignments with the mock sidebar and no top-bar tabs', async () => {
    smeSession()
    renderApp('/')
    expect(
      await screen.findByRole('heading', { name: 'My Assignments', level: 1 }),
    ).toBeInTheDocument()
    const sidebar = screen.getByRole('navigation', { name: 'SME sections' })
    expect(
      within(sidebar)
        .getAllByRole('group')
        .map((g) => g.getAttribute('aria-label')),
    ).toEqual(GROUPS.map(([label]) => label))
    for (const [label, items] of GROUPS) {
      expect(linksOf(within(sidebar).getByRole('group', { name: label }))).toEqual(items)
    }
    expect(within(sidebar).getByRole('link', { name: /My Assignments/ })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(await within(sidebar).findByText('1 Active')).toBeInTheDocument()
    // Name and first subject at the foot.
    expect(await screen.findByText('Physics SME')).toBeInTheDocument()
    expect(screen.getAllByText('Asha Rao').length).toBeGreaterThan(0)
    // No top bar: neither the tab bar nor its mobile row.
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
    expect(screen.queryByRole('navigation', { name: 'Main (mobile)' })).toBeNull()
  })

  it('links the older screens until their replacements are built', async () => {
    smeSession()
    renderApp('/sme')
    const older = await screen.findByRole('group', { name: 'Older screens' })
    expect(
      within(older)
        .getAllByRole('link')
        .map((l) => l.getAttribute('href')),
    ).toEqual(['/qna', '/schema', '/evaluated', '/evaluate'])
  })

  it('has a header with the tab title, a placeholder search and bell, and the user menu', async () => {
    smeSession()
    const user = userEvent.setup()
    renderApp('/sme')
    const banner = await screen.findByRole('banner')
    expect(within(banner).getByText('My Assignments')).toBeInTheDocument()
    const search = within(banner).getByRole('searchbox', { name: /Search/ })
    expect(search).toBeDisabled()
    expect(search).toHaveAttribute('title', expect.stringMatching(/later update/))
    const bell = within(banner).getByRole('button', { name: 'Notifications' })
    expect(bell).toBeDisabled()
    expect(bell).toHaveAttribute('title', expect.stringMatching(/later update/))
    expect(within(banner).getByRole('button', { name: /Account menu for Asha Rao/ })).toBeVisible()

    const sidebar = screen.getByRole('navigation', { name: 'SME sections' })
    await user.click(within(sidebar).getByRole('link', { name: 'My Reports' }))
    expect(await within(banner).findByText('My Reports')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Your reports come later' })).toBeInTheDocument()
  })

  it('shows a short empty state for each section not built yet', async () => {
    smeSession()
    const user = userEvent.setup()
    renderApp('/sme')
    const sidebar = await screen.findByRole('navigation', { name: 'SME sections' })
    const empty: [string, string][] = [
      ['Manage Question Papers', 'Question papers come next'],
      ['Manage Schema', 'Schemas come next'],
      ['My Profile', 'Your profile page comes later'],
      ['My Reports', 'Your reports come later'],
    ]
    for (const [link, text] of empty) {
      await user.click(within(sidebar).getByRole('link', { name: link }))
      expect(await screen.findByRole('heading', { name: link, level: 1 })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: text })).toBeInTheDocument()
    }
  })

  it('opens the sidebar from the menu button and gives focus back on Escape', async () => {
    smeSession()
    const user = userEvent.setup()
    renderApp('/sme')
    const button = await screen.findByRole('button', { name: 'Menu' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    const sidebar = screen.getByRole('navigation', { name: 'SME sections' })
    const links = within(sidebar).getAllByRole('link')
    await waitFor(() => expect(links[0]).toHaveFocus())
    // Tab stays inside the open drawer.
    await user.tab({ shift: true })
    expect(links.at(-1)).toHaveFocus()
    await user.tab()
    expect(links[0]).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(button).toHaveFocus()

    // Following a link closes it too.
    await user.click(button)
    await user.click(within(sidebar).getByRole('link', { name: 'My Profile' }))
    expect(await screen.findByRole('heading', { name: 'My Profile', level: 1 })).toBeVisible()
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('sends an unknown /sme address to My Assignments', async () => {
    smeSession()
    renderApp('/sme/nothing-here')
    expect(
      await screen.findByRole('heading', { name: 'My Assignments', level: 1 }),
    ).toBeInTheDocument()
  })

  it('renders the evaluation steps inside the SME frame', async () => {
    smeSession()
    renderApp('/evaluate?step=3')
    expect(await screen.findByRole('heading', { name: 'Choose a booklet first' })).toBeVisible()
    expect(screen.getByRole('navigation', { name: 'SME sections' })).toBeInTheDocument()
    expect(within(screen.getByRole('banner')).getByText('AI Evaluation')).toBeInTheDocument()
  })

  it('leaves the admin unchanged: admin sidebar, no SME frame', async () => {
    adminSession()
    renderApp('/admin')
    expect(await screen.findByRole('heading', { name: 'Dashboard Overview' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Admin sections' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'SME sections' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Menu' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Q&A DB' })).toBeNull()
  })
})
