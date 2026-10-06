/** Geometry of a recognised diagram's graph for the graph editor (docs/api/diagram-graph.schema.json). */
import type { components } from '../api/schema'

export type DiagramGraph = components['schemas']['DiagramGraphOut']
export type GraphNode = components['schemas']['GraphNodeOut']
export type GraphEdge = components['schemas']['GraphEdgeOut']
export type GraphEdit = components['schemas']['GraphEditIn']
export type ShapeName = GraphNode['shape']

export type Rect = [number, number, number, number]
export interface View {
  x: number
  y: number
  width: number
  height: number
}

export const SHAPES: { value: ShapeName; label: string }[] = [
  { value: 'terminal', label: 'Start / end' },
  { value: 'process', label: 'Process' },
  { value: 'decision', label: 'Decision' },
  { value: 'io', label: 'Input / output' },
  { value: 'circle', label: 'Circle' },
  { value: 'block', label: 'Block' },
  { value: 'other', label: 'Other' },
]

export function shapeLabel(shape: ShapeName): string {
  return SHAPES.find((s) => s.value === shape)?.label ?? shape
}

/** What the SVG shows: the crop, or the whole image. */
export function viewOf(
  crop: Rect | null | undefined,
  size: { width: number; height: number },
): View {
  if (crop && crop[2] > crop[0] && crop[3] > crop[1]) {
    return { x: crop[0], y: crop[1], width: crop[2] - crop[0], height: crop[3] - crop[1] }
  }
  return { x: 0, y: 0, width: size.width, height: size.height }
}

export function centreOf(box: number[]): [number, number] {
  return [((box[0] ?? 0) + (box[2] ?? 0)) / 2, ((box[1] ?? 0) + (box[3] ?? 0)) / 2]
}

/** Where a node's outline meets the line towards `towards` (so arrows end at the shape). */
function onBoundary(box: number[], towards: [number, number]): [number, number] {
  const [cx, cy] = centreOf(box)
  const hw = Math.max(1, ((box[2] ?? 0) - (box[0] ?? 0)) / 2)
  const hh = Math.max(1, ((box[3] ?? 0) - (box[1] ?? 0)) / 2)
  const dx = towards[0] - cx
  const dy = towards[1] - cy
  if (dx === 0 && dy === 0) return [cx, cy]
  const scale = Math.min(hw / Math.abs(dx || 1e-9), hh / Math.abs(dy || 1e-9))
  return [cx + dx * scale, cy + dy * scale]
}

/** Both ends of an edge in image pixels, or null when nothing says where it is. */
export function edgeLine(
  graph: DiagramGraph,
  edge: GraphEdge,
): { from: [number, number]; to: [number, number] } | null {
  const source = edge.source ? graph.nodes.find((n) => n.id === edge.source) : undefined
  const target = edge.target ? graph.nodes.find((n) => n.id === edge.target) : undefined
  const tail = edge.tail ? ([edge.tail[0] ?? 0, edge.tail[1] ?? 0] as [number, number]) : null
  const head = edge.head ? ([edge.head[0] ?? 0, edge.head[1] ?? 0] as [number, number]) : null
  if (source?.box && target?.box) {
    const a = centreOf(source.box)
    const b = centreOf(target.box)
    return { from: onBoundary(source.box, b), to: onBoundary(target.box, a) }
  }
  if (tail && head) return { from: tail, to: head }
  if (source?.box && head) return { from: onBoundary(source.box, head), to: head }
  if (target?.box && tail) return { from: tail, to: onBoundary(target.box, tail) }
  if (edge.box) {
    return { from: [edge.box[0] ?? 0, edge.box[1] ?? 0], to: [edge.box[2] ?? 0, edge.box[3] ?? 0] }
  }
  return null
}

/** A new node's box when the teacher clicks at (x, y): a tenth of the view, centred there. */
export function placedBox(view: View, x: number, y: number): Rect {
  const w = Math.max(8, Math.round(view.width * 0.16))
  const h = Math.max(6, Math.round(view.height * 0.1))
  const x0 = Math.max(0, Math.round(x - w / 2))
  const y0 = Math.max(0, Math.round(y - h / 2))
  return [x0, y0, x0 + w, y0 + h]
}

/** The text of a node or edge for lists and screen readers. */
export function nodeName(node: GraphNode): string {
  return node.label.trim() ? node.label : `(no label, ${shapeLabel(node.shape).toLowerCase()})`
}

export function edgeName(graph: DiagramGraph, edge: GraphEdge): string {
  const name = (id: string | null) => {
    const node = id ? graph.nodes.find((n) => n.id === id) : undefined
    return node ? nodeName(node) : 'nothing'
  }
  const arrow = edge.directed ? '→' : '—'
  const text = `${name(edge.source)} ${arrow} ${name(edge.target)}`
  return edge.label.trim() ? `${text} (${edge.label})` : text
}
