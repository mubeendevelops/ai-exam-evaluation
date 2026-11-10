import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderApp } from '../../test/render'
import { paperCardOf } from '../../test/schemaFixtures'
import { smeSession } from '../../test/smeFixtures'

const pdf = vi.hoisted(() => ({
  draws: [] as { page: number; scale: number }[],
  opened: [] as number[],
  closed: 0,
}))

vi.mock('../../lib/paperPdf', () => ({
  openPdf: vi.fn(async (data: ArrayBuffer) => {
    pdf.opened.push(data.byteLength)
    return {
      pages: 2,
      draw: (page: number, _canvas: HTMLCanvasElement, scale: number) => {
        pdf.draws.push({ page, scale })
        return { done: Promise.resolve(), cancel: () => undefined }
      },
      close: () => {
        pdf.closed += 1
      },
    }
  }),
}))

const BYTES = '%PDF-1.7 fake paper'
const PDF_ROUTE = 'GET /api/v1/blueprints/paper-1/versions/2/paper.pdf'

beforeEach(() => {
  pdf.draws.length = 0
  pdf.opened.length = 0
  pdf.closed = 0
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function session(extra: Parameters<typeof smeSession>[0] = {}) {
  return smeSession({
    'GET /api/v1/papers': {
      json: { items: [paperCardOf()], total: 1, limit: 12, offset: 0, descriptors: [] },
    },
    [PDF_ROUTE]: { body: BYTES, headers: { 'Content-Type': 'application/pdf' } },
    ...extra,
  })
}

async function openPreview(user: ReturnType<typeof userEvent.setup>) {
  renderApp('/sme/papers')
  await user.click(await screen.findByRole('button', { name: 'Preview Physics Mid Term 2026' }))
  return screen.findByRole('dialog', { name: 'Physics Mid Term 2026 - Preview' })
}

function lastScale(): number | undefined {
  return pdf.draws.at(-1)?.scale
}

describe('the question paper preview (P34, mock modal 5)', () => {
  it('draws every page of the server’s PDF of that version', async () => {
    const mock = session()
    const user = userEvent.setup()
    const dialog = await openPreview(user)
    expect(
      await within(dialog).findByRole('document', { name: 'Physics Mid Term 2026, 2 pages' }),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole('img', { name: 'Page 1 of 2' })).toBeInTheDocument()
    expect(within(dialog).getByRole('img', { name: 'Page 2 of 2' })).toBeInTheDocument()
    expect(mock.callsTo(PDF_ROUTE)).toHaveLength(1)
    expect(pdf.opened).toEqual([BYTES.length])
    await waitFor(() => expect(new Set(pdf.draws.map((d) => d.page))).toEqual(new Set([1, 2])))
    expect(dialog).toHaveTextContent('Version 2 · PHY-MID-26')
  })

  it('zooms out and in by 10 % between 50 % and 180 %, and resets to 100 %', async () => {
    session()
    const user = userEvent.setup()
    const dialog = await openPreview(user)
    await within(dialog).findByRole('document')
    const zoom = within(dialog).getByRole('group', { name: 'Zoom' })
    const out = within(zoom).getByRole('button', { name: 'Zoom out' })
    const zoomIn = within(zoom).getByRole('button', { name: 'Zoom in' })
    const reset = within(zoom).getByRole('button', { name: 'Reset' })
    expect(zoom).toHaveTextContent('100%')
    expect(reset).toBeDisabled()
    await waitFor(() => expect(lastScale()).toBeCloseTo(1.25))
    await user.click(zoomIn)
    expect(zoom).toHaveTextContent('110%')
    await waitFor(() => expect(lastScale()).toBeCloseTo(1.375))
    for (let i = 0; i < 10; i++) await user.click(out)
    expect(zoom).toHaveTextContent('50%')
    expect(out).toBeDisabled()
    for (let i = 0; i < 20; i++) if (!zoomIn.hasAttribute('disabled')) await user.click(zoomIn)
    expect(zoom).toHaveTextContent('180%')
    expect(zoomIn).toBeDisabled()
    await user.click(reset)
    expect(zoom).toHaveTextContent('100%')
    await waitFor(() => expect(lastScale()).toBeCloseTo(1.25))
  })

  it('downloads the same file under the server’s name', async () => {
    const mock = session()
    const created = vi.fn(() => 'blob:paper')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() }))
    const clicked: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.download)
    })
    const user = userEvent.setup()
    const dialog = await openPreview(user)
    await within(dialog).findByRole('document')
    await user.click(within(dialog).getByRole('button', { name: 'Download PDF' }))
    expect(clicked).toEqual(['paper-PHY-MID-26-v2.pdf'])
    expect(mock.callsTo(PDF_ROUTE)).toHaveLength(1) // the preview's bytes, not a second fetch
    // The card's own button fetches it.
    await user.keyboard('{Escape}')
    await user.click(
      screen.getByRole('button', { name: 'Download the PDF of Physics Mid Term 2026' }),
    )
    await waitFor(() => expect(clicked).toHaveLength(2))
    expect(mock.callsTo(PDF_ROUTE)).toHaveLength(2)
    expect(pdf.closed).toBeGreaterThan(0)
  })

  it('says so when the paper cannot be shown', async () => {
    session({ [PDF_ROUTE]: { status: 404, json: { detail: 'paper not found' } } })
    const user = userEvent.setup()
    const dialog = await openPreview(user)
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'The paper could not be shown',
    )
    expect(within(dialog).getByRole('button', { name: 'Download PDF' })).toBeDisabled()
  })
})
