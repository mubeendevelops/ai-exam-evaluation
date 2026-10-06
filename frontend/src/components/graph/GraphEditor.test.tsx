import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { graph } from '../../test/evaluateFixtures'
import { mockApi } from '../../test/mockApi'
import type { DiagramGraph, GraphEdit } from '../../lib/graph'
import { GraphEditor } from './GraphEditor'

beforeEach(() => {
  Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  mockApi({
    '/api/v1/x': {},
    'GET /api/v1/pic.png': { body: new Blob(['x'], { type: 'image/png' }) },
  })
})
afterEach(() => vi.unstubAllGlobals())

function setup(
  over: {
    graph?: DiagramGraph
    readOnlyReason?: string
    save?: boolean
    crop?: [number, number, number, number]
  } = {},
) {
  const sent: GraphEdit[][] = []
  const onEdit = vi.fn(async (edits: GraphEdit[]) => {
    sent.push(edits)
    return over.save ?? true
  })
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <GraphEditor
        title="Diagram in question 3"
        imagePath="/api/v1/pic.png"
        imageSize={{ width: 1000, height: 700 }}
        crop={over.crop}
        graph={over.graph ?? graph()}
        readOnlyReason={over.readOnlyReason}
        onEdit={onEdit}
      />
    </QueryClientProvider>,
  )
  return { sent, onEdit, user: userEvent.setup() }
}

describe('graph editor', () => {
  it('draws the nodes and edges over the picture and lists them', () => {
    setup()
    const canvas = screen.getByRole('group', { name: /nodes and edges over the drawing/ })
    expect(canvas.querySelectorAll('[data-node]')).toHaveLength(2)
    expect(canvas.querySelectorAll('[data-edge]')).toHaveLength(1)
    expect(within(canvas).getByText('Start')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Node Start' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Edge Start → Add' })).toBeVisible()
  })

  it('shows only the diagram crop of a page', () => {
    setup({ crop: [50, 60, 450, 360] })
    expect(screen.getByRole('group', { name: /over the drawing/ })).toHaveAttribute(
      'viewBox',
      '50 60 400 300',
    )
  })

  it('relabels a node: one edit with the node and the new text', async () => {
    const { user, sent } = setup()
    await user.click(screen.getByRole('button', { name: 'Node Add' }))
    const label = screen.getByRole('textbox', { name: 'Node label' })
    expect(screen.getByRole('button', { name: 'Save label' })).toBeDisabled()
    await user.clear(label)
    await user.type(label, 'Sum')
    await user.click(screen.getByRole('button', { name: 'Save label' }))
    expect(sent).toEqual([[{ op: 'relabel_node', id: 'n2', label: 'Sum' }]])
  })

  it('selects a node by clicking it on the drawing', async () => {
    const { user } = setup()
    const node = screen
      .getByRole('group', { name: /over the drawing/ })
      .querySelector('[data-node="n1"]')!
    await user.click(node)
    expect(screen.getByRole('textbox', { name: 'Node label' })).toHaveValue('Start')
    expect(screen.getByRole('button', { name: 'Node Start' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('changes the shape and removes a node', async () => {
    const { user, sent } = setup()
    await user.click(screen.getByRole('button', { name: 'Node Add' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Shape' }), 'decision')
    await user.click(screen.getByRole('button', { name: 'Remove node' }))
    expect(sent).toEqual([
      [{ op: 'reshape_node', id: 'n2', shape: 'decision' }],
      [{ op: 'remove_node', id: 'n2' }],
    ])
  })

  it('reverses an arrow, takes its head off, relabels and removes it', async () => {
    const { user, sent } = setup()
    await user.click(screen.getByRole('button', { name: 'Edge Start → Add' }))
    await user.click(screen.getByRole('button', { name: 'Reverse arrow' }))
    await user.click(screen.getByRole('button', { name: 'Remove arrowhead' }))
    await user.type(screen.getByRole('textbox', { name: 'Edge label' }), 'yes')
    await user.click(screen.getByRole('button', { name: 'Save label' }))
    await user.click(screen.getByRole('button', { name: 'Remove edge' }))
    expect(sent).toEqual([
      [{ op: 'reverse_edge', id: 'e1' }],
      [{ op: 'set_edge_ends', id: 'e1', source: 'n1', target: 'n2', directed: false }],
      [{ op: 'relabel_edge', id: 'e1', label: 'yes' }],
      [{ op: 'remove_edge', id: 'e1' }],
    ])
  })

  it('adds an edge between two chosen nodes', async () => {
    const { user, sent } = setup()
    const add = screen.getByRole('button', { name: 'Add edge' })
    expect(add).toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Edge from' }), 'n2')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Edge to' }), 'n1')
    await user.type(screen.getByRole('textbox', { name: 'New edge label' }), 'loop')
    await user.click(add)
    expect(sent).toEqual([
      [{ op: 'add_edge', source: 'n2', target: 'n1', label: 'loop', directed: true }],
    ])
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Edge from' })).toHaveValue(''))
  })

  it('adds a node at the centre of the view, or where the teacher clicks', async () => {
    const { user, sent } = setup({ crop: [0, 0, 400, 200] })
    await user.selectOptions(screen.getByRole('combobox', { name: 'New node shape' }), 'decision')
    await user.type(screen.getByRole('textbox', { name: 'New node label' }), 'Is it done?')
    await user.click(screen.getByRole('button', { name: 'Add at centre' }))
    expect(sent[0]).toEqual([
      { op: 'add_node', shape: 'decision', label: 'Is it done?', box: [168, 90, 232, 110] },
    ])
    await user.click(screen.getByRole('button', { name: 'Place on drawing' }))
    expect(screen.getByText('Click where the new node goes.')).toBeVisible()
  })

  it('places a new node where the teacher clicks the drawing', async () => {
    const { user, sent } = setup({ crop: [0, 0, 400, 200] })
    const canvas = screen.getByRole('group', { name: /over the drawing/ })
    canvas.getBoundingClientRect = () => ({ left: 10, top: 20, width: 200, height: 100 }) as DOMRect
    await user.type(screen.getByRole('textbox', { name: 'New node label' }), 'Check')
    await user.click(screen.getByRole('button', { name: 'Place on drawing' }))
    await user.pointer({
      target: canvas,
      keys: '[MouseLeft]',
      coords: { clientX: 60, clientY: 45 },
    })
    // (60 - 10) / 200 of 400 = 100 across, (45 - 20) / 100 of 200 = 50 down
    await waitFor(() =>
      expect(sent).toEqual([
        [{ op: 'add_node', shape: 'process', label: 'Check', box: [68, 40, 132, 60] }],
      ]),
    )
  })

  it('cannot edit while another teacher has the booklet open', async () => {
    const { user, sent } = setup({ readOnlyReason: 'Dr. Other has this booklet open.' })
    expect(screen.getByRole('note')).toHaveTextContent('Dr. Other has this booklet open.')
    await user.click(screen.getByRole('button', { name: 'Node Add' }))
    expect(screen.getByRole('button', { name: 'Save label' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Add at centre' })).toBeDisabled()
    expect(sent).toEqual([])
  })

  it('says a node has no label, flags loose ends and an empty graph', () => {
    const loose = graph({
      nodes: [{ ...graph().nodes[0]!, label: '' }],
      edges: [{ ...graph().edges[0]!, target: null }],
      edited_by_teacher: true,
    })
    setup({ graph: loose })
    expect(screen.getByRole('button', { name: 'Node (no label, start / end)' })).toBeVisible()
    expect(screen.getByText('(loose end)')).toBeVisible()
    expect(screen.getByText('Corrected by a teacher.')).toBeVisible()
  })

  it('invites the teacher to draw when nothing was found', () => {
    setup({ graph: graph({ nodes: [], edges: [] }) })
    expect(screen.getByText('No nodes were found. Add them below.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Add edge' })).toBeDisabled()
  })
})
