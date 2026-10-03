import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  Badge,
  EmptyState,
  GlassPanel,
  Modal,
  Pill,
  Stepper,
  TabNav,
  ToastProvider,
  useToast,
} from '.'

afterEach(() => vi.useRealTimers())

function ModalHarness({ onClose = () => {} }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      <Modal
        open={open}
        onClose={() => {
          onClose()
          setOpen(false)
        }}
        title="Dialog title"
        eyebrow="Eyebrow"
      >
        <input aria-label="first" />
        <button>last</button>
      </Modal>
    </>
  )
}

describe('Modal', () => {
  it('is a labelled dialog, closes on Escape and returns focus to the opener', async () => {
    const user = userEvent.setup()
    render(<ModalHarness />)
    expect(screen.queryByRole('dialog')).toBeNull()
    const opener = screen.getByRole('button', { name: 'Open' })
    await user.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Dialog title' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(opener).toHaveFocus()
  })

  it('closes on the close button and on a click on the backdrop, not on the panel', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<ModalHarness onClose={onClose} />)
    await user.click(screen.getByRole('button', { name: 'Open' }))
    await user.click(screen.getByRole('dialog'))
    expect(onClose).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: 'Open' }))
    await user.click(screen.getByRole('dialog').parentElement!)
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('keeps Tab inside the dialog', async () => {
    const user = userEvent.setup()
    render(<ModalHarness />)
    await user.click(screen.getByRole('button', { name: 'Open' }))
    const last = screen.getByRole('button', { name: 'last' })
    last.focus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    await user.tab({ shift: true })
    expect(last).toHaveFocus()
  })
})

function ToastButton() {
  const toast = useToast()
  return <button onClick={() => toast.show('Saved it', 'emerald')}>Go</button>
}

describe('Toast', () => {
  it('announces a message, lets it be dismissed, and removes it after a while', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(
      <ToastProvider>
        <ToastButton />
      </ToastProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'Go' }))
    expect(screen.getByText('Saved it')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText('Saved it')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Go' }))
    expect(screen.getByText('Saved it')).toBeInTheDocument()
    await act(async () => {
      vi.advanceTimersByTime(5100)
    })
    expect(screen.queryByText('Saved it')).toBeNull()
  })

  it('needs its provider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<ToastButton />)).toThrow(/ToastProvider/)
    spy.mockRestore()
  })
})

describe('Stepper', () => {
  it('numbers the steps, marks the current one and reports a selection', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<Stepper steps={['Scan Upload', 'AI Segmentation']} current={0} onSelect={onSelect} />)
    expect(screen.getByRole('button', { name: '1. Scan Upload' })).toHaveAttribute(
      'aria-current',
      'step',
    )
    expect(screen.getByRole('button', { name: '2. AI Segmentation' })).not.toHaveAttribute(
      'aria-current',
    )
    await user.click(screen.getByRole('button', { name: '2. AI Segmentation' }))
    expect(onSelect).toHaveBeenCalledWith(1)
  })

  it('is read-only without onSelect', () => {
    render(<Stepper steps={['A', 'B']} current={1} />)
    expect(screen.getByRole('button', { name: '1. A' })).toBeDisabled()
  })
})

describe('TabNav', () => {
  const items = [
    { to: '/qna', label: 'Q&A DB', icon: 'fa-solid fa-database' },
    {
      to: '/evaluate',
      label: 'AI Evaluation',
      shortLabel: 'AI Eval',
      icon: 'fa-solid fa-microchip',
    },
  ]
  it('marks the tab of the current route; mobile uses the short label', () => {
    render(
      <MemoryRouter initialEntries={['/evaluate']}>
        <TabNav items={items} variant="desktop" />
        <TabNav items={items} variant="mobile" />
      </MemoryRouter>,
    )
    const desktop = screen.getByRole('navigation', { name: 'Main' })
    const mobile = screen.getByRole('navigation', { name: 'Main (mobile)' })
    expect(desktop.querySelector('[aria-current="page"]')).toHaveTextContent('AI Evaluation')
    expect(mobile.querySelector('[aria-current="page"]')).toHaveTextContent('AI Eval')
    expect(mobile).toHaveTextContent('Q&A DB')
  })
})

describe('small components', () => {
  it('EmptyState shows title, text and action', () => {
    render(
      <EmptyState icon="fa-solid fa-database" title="Nothing yet" action={<button>Add</button>}>
        Add the first one.
      </EmptyState>,
    )
    expect(screen.getByRole('heading', { name: 'Nothing yet' })).toBeInTheDocument()
    expect(screen.getByText('Add the first one.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument()
  })

  it('Pill is a status with a title; Badge and GlassPanel render their children', () => {
    render(
      <>
        <Pill tone="amber" title="details">
          Worker offline
        </Pill>
        <Badge tone="red">locked</Badge>
        <GlassPanel as="section" aria-label="panel">
          inside
        </GlassPanel>
      </>,
    )
    expect(screen.getByRole('status')).toHaveAttribute('title', 'details')
    expect(screen.getByText('locked')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'panel' })).toHaveTextContent('inside')
  })
})
