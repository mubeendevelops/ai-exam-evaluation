import { useEffect, useId, useRef, useState, type MouseEvent } from 'react'
import {
  SHAPES,
  centreOf,
  edgeLine,
  edgeName,
  nodeName,
  placedBox,
  viewOf,
  type DiagramGraph,
  type GraphEdit,
  type GraphEdge,
  type GraphNode,
  type Rect,
  type ShapeName,
} from '../../lib/graph'
import { useFileUrl } from '../qna/AuthImage'
import { SmallButton, compactInputClass } from '../schema/controls'

type Selected = { kind: 'node' | 'edge'; id: string } | null

interface Props {
  title: string
  /** The picture the graph was read from (behind the bearer token). */
  imagePath: string
  /** Size of that picture in pixels; measured from the file when not given. */
  imageSize?: { width: number; height: number }
  /** Show only this area (a student's diagram on its page), `[x0, y0, x1, y1]` in pixels. */
  crop?: Rect | null
  graph: DiagramGraph
  /** Why the graph cannot be edited now (another teacher's lock, an approved answer…). */
  readOnlyReason?: string
  /** Sends the edits to the API; true when they were saved. */
  onEdit: (edits: GraphEdit[]) => Promise<boolean>
}

const FALLBACK_SIZE = { width: 1000, height: 700 }

function useImageSize(url: string | undefined, given?: { width: number; height: number }) {
  const [measured, setMeasured] = useState<{ width: number; height: number } | null>(null)
  useEffect(() => {
    if (given || !url) return
    let live = true
    const image = new Image()
    image.onload = () => {
      if (live) setMeasured({ width: image.naturalWidth, height: image.naturalHeight })
    }
    image.src = url
    return () => {
      live = false
    }
  }, [url, given])
  return given ?? measured ?? FALLBACK_SIZE
}

function ShapeOutline({ node, selected }: { node: GraphNode; selected: boolean }) {
  const box = node.box
  if (!box) return null
  const [x0, y0, x1, y1] = [box[0] ?? 0, box[1] ?? 0, box[2] ?? 0, box[3] ?? 0]
  const w = x1 - x0
  const h = y1 - y0
  const common = {
    fill: selected ? 'rgba(139,92,246,0.35)' : 'rgba(6,182,212,0.18)',
    stroke: selected ? '#c4b5fd' : '#22d3ee',
    strokeWidth: selected ? 3 : 2,
    vectorEffect: 'non-scaling-stroke' as const,
  }
  switch (node.shape) {
    case 'decision':
      return (
        <polygon
          {...common}
          points={`${x0 + w / 2},${y0} ${x1},${y0 + h / 2} ${x0 + w / 2},${y1} ${x0},${y0 + h / 2}`}
        />
      )
    case 'io': {
      const skew = w * 0.15
      return (
        <polygon
          {...common}
          points={`${x0 + skew},${y0} ${x1},${y0} ${x1 - skew},${y1} ${x0},${y1}`}
        />
      )
    }
    case 'circle':
      return <ellipse {...common} cx={x0 + w / 2} cy={y0 + h / 2} rx={w / 2} ry={h / 2} />
    case 'terminal':
      return <rect {...common} x={x0} y={y0} width={w} height={h} rx={h / 2} />
    case 'other':
      return <rect {...common} x={x0} y={y0} width={w} height={h} strokeDasharray="6 4" />
    default:
      return <rect {...common} x={x0} y={y0} width={w} height={h} rx={3} />
  }
}

/** The label, shape and removal of the selected node or edge. Keyed by the selection and its
 * saved label, so the text box starts from what the server holds. */
function SelectionTools({
  node,
  edge,
  disabled,
  send,
}: {
  node: GraphNode | undefined
  edge: GraphEdge | undefined
  disabled: boolean
  send: (edits: GraphEdit[]) => Promise<boolean>
}) {
  const saved = node?.label ?? edge?.label ?? ''
  const [draft, setDraft] = useState(saved)
  return (
    <fieldset
      disabled={disabled}
      className="space-y-2 rounded-xl border border-purple-500/30 bg-purple-950/20 p-3"
    >
      <legend className="px-1 font-semibold text-purple-300">
        {node ? 'Selected node' : 'Selected edge'}
      </legend>
      <label className="block text-gray-300">
        {node ? 'Node label' : 'Edge label'}
        <input
          className={`${compactInputClass} mt-1`}
          value={draft}
          maxLength={200}
          onChange={(e) => setDraft(e.target.value)}
        />
      </label>
      {node && (
        <label className="block text-gray-300">
          Shape
          <select
            className={`${compactInputClass} mt-1`}
            value={node.shape}
            onChange={(e) =>
              void send([{ op: 'reshape_node', id: node.id, shape: e.target.value as ShapeName }])
            }
          >
            {SHAPES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="flex flex-wrap gap-2">
        <SmallButton
          icon="fa-solid fa-check"
          disabled={draft === saved}
          onClick={() =>
            void send([
              node
                ? { op: 'relabel_node', id: node.id, label: draft }
                : { op: 'relabel_edge', id: edge?.id, label: draft },
            ])
          }
        >
          Save label
        </SmallButton>
        {edge && (
          <>
            <SmallButton
              icon="fa-solid fa-right-left"
              onClick={() => void send([{ op: 'reverse_edge', id: edge.id }])}
            >
              Reverse arrow
            </SmallButton>
            <SmallButton
              icon="fa-solid fa-arrow-right-long"
              onClick={() =>
                void send([
                  {
                    op: 'set_edge_ends',
                    id: edge.id,
                    source: edge.source,
                    target: edge.target,
                    directed: !edge.directed,
                  },
                ])
              }
            >
              {edge.directed ? 'Remove arrowhead' : 'Add arrowhead'}
            </SmallButton>
          </>
        )}
        <SmallButton
          icon="fa-solid fa-trash"
          tone="gray"
          onClick={() =>
            void send([
              node ? { op: 'remove_node', id: node.id } : { op: 'remove_edge', id: edge?.id },
            ])
          }
        >
          {node ? 'Remove node' : 'Remove edge'}
        </SmallButton>
      </div>
    </fieldset>
  )
}

/**
 * The graph editor (U6 Q20): the recognised nodes and edges drawn over the diagram, with the
 * teacher's tools: relabel, reshape, add and remove nodes; add, relabel, reverse and remove
 * edges. Every action is one call to the API (`onEdit`); the graph shown is whatever the
 * server last returned. The lists beside the picture do the same as clicking the picture, for
 * the keyboard and screen readers. Used for a student's drawing in the segmentation step and
 * for a reference diagram in the question bank.
 */
export function GraphEditor({
  title,
  imagePath,
  imageSize,
  crop,
  graph,
  readOnlyReason,
  onEdit,
}: Props) {
  const file = useFileUrl(imagePath)
  const size = useImageSize(file.data, imageSize)
  const view = viewOf(crop, size)
  const [selected, setSelected] = useState<Selected>(null)
  const [busy, setBusy] = useState(false)
  const [placing, setPlacing] = useState<{ shape: ShapeName; label: string } | null>(null)
  const [newShape, setNewShape] = useState<ShapeName>('process')
  const [newLabel, setNewLabel] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [edgeLabel, setEdgeLabel] = useState('')
  const svg = useRef<SVGSVGElement>(null)
  const ids = useId()
  const readOnly = readOnlyReason !== undefined

  const node = selected?.kind === 'node' ? graph.nodes.find((n) => n.id === selected.id) : undefined
  const edge = selected?.kind === 'edge' ? graph.edges.find((e) => e.id === selected.id) : undefined
  const selectedKey = node ? `n:${node.id}:${node.label}` : edge ? `e:${edge.id}:${edge.label}` : ''

  async function send(edits: GraphEdit[]): Promise<boolean> {
    if (readOnly || busy) return false
    setBusy(true)
    try {
      return await onEdit(edits)
    } finally {
      setBusy(false)
    }
  }

  function place(event: MouseEvent<SVGSVGElement>) {
    if (!placing || !svg.current) return
    const rect = svg.current.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return
    const x = view.x + ((event.clientX - rect.left) / rect.width) * view.width
    const y = view.y + ((event.clientY - rect.top) / rect.height) * view.height
    const { shape, label } = placing
    setPlacing(null)
    void send([{ op: 'add_node', shape, label, box: placedBox(view, x, y) }]).then((ok) => {
      if (ok) setNewLabel('')
    })
  }

  const fontSize = view.width / 38
  const marker = view.width / 55

  return (
    <section aria-label={title} className="space-y-3">
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="relative overflow-hidden rounded-xl border border-gray-800 bg-gray-950 lg:col-span-3">
          <svg
            ref={svg}
            role="group"
            aria-label={`${title}: nodes and edges over the drawing`}
            viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
            className={`block w-full bg-white ${placing ? 'cursor-crosshair' : ''}`}
            style={{ aspectRatio: `${view.width} / ${view.height}` }}
            onClick={place}
          >
            <defs>
              <marker
                id={`${ids}-arrow`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerUnits="userSpaceOnUse"
                markerWidth={marker}
                markerHeight={marker}
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#0e7490" />
              </marker>
            </defs>
            {file.data && (
              <image href={file.data} x={0} y={0} width={size.width} height={size.height} />
            )}
            {graph.edges.map((e) => {
              const line = edgeLine(graph, e)
              if (!line) return null
              const on = selected?.kind === 'edge' && selected.id === e.id
              return (
                <g key={e.id} data-edge={e.id}>
                  <line
                    x1={line.from[0]}
                    y1={line.from[1]}
                    x2={line.to[0]}
                    y2={line.to[1]}
                    stroke={on ? '#7c3aed' : '#0e7490'}
                    strokeWidth={on ? 4 : 2.5}
                    vectorEffect="non-scaling-stroke"
                    markerEnd={e.directed ? `url(#${ids}-arrow)` : undefined}
                  />
                  <line
                    x1={line.from[0]}
                    y1={line.from[1]}
                    x2={line.to[0]}
                    y2={line.to[1]}
                    stroke="transparent"
                    strokeWidth={14}
                    vectorEffect="non-scaling-stroke"
                    className="cursor-pointer"
                    onClick={(event) => {
                      event.stopPropagation()
                      setSelected({ kind: 'edge', id: e.id })
                    }}
                  />
                  {e.label && (
                    <text
                      x={(line.from[0] + line.to[0]) / 2}
                      y={(line.from[1] + line.to[1]) / 2 - fontSize / 3}
                      fontSize={fontSize * 0.8}
                      textAnchor="middle"
                      fill="#4c1d95"
                      stroke="#fff"
                      strokeWidth={fontSize / 5}
                      paintOrder="stroke"
                    >
                      {e.label}
                    </text>
                  )}
                </g>
              )
            })}
            {graph.nodes.map((n) => {
              if (!n.box) return null
              const [cx, cy] = centreOf(n.box)
              const on = selected?.kind === 'node' && selected.id === n.id
              return (
                <g
                  key={n.id}
                  data-node={n.id}
                  className="cursor-pointer"
                  onClick={(event) => {
                    event.stopPropagation()
                    setSelected({ kind: 'node', id: n.id })
                  }}
                >
                  <ShapeOutline node={n} selected={on} />
                  <text
                    x={cx}
                    y={cy}
                    fontSize={fontSize}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fill="#0f172a"
                    stroke="#fff"
                    strokeWidth={fontSize / 6}
                    paintOrder="stroke"
                  >
                    {n.label}
                  </text>
                </g>
              )
            })}
          </svg>
          {placing && (
            <p className="absolute right-2 bottom-2 left-2 rounded-lg bg-black/70 px-3 py-1.5 text-center text-[11px] text-cyan-200">
              Click where the new node goes.
            </p>
          )}
        </div>

        <div className="space-y-3 text-xs lg:col-span-2">
          {readOnlyReason && (
            <p
              role="note"
              className="rounded-lg border border-amber-500/30 bg-amber-950/30 px-3 py-2 text-amber-200"
            >
              {readOnlyReason}
            </p>
          )}
          <div>
            <h4 className="mb-1 font-semibold text-gray-300">
              Nodes <span className="font-normal text-gray-500">({graph.nodes.length})</span>
            </h4>
            {graph.nodes.length === 0 ? (
              <p className="text-gray-500">No nodes were found. Add them below.</p>
            ) : (
              <ul className="flex flex-wrap gap-1">
                {graph.nodes.map((n) => (
                  <li key={n.id}>
                    <button
                      type="button"
                      aria-pressed={selected?.kind === 'node' && selected.id === n.id}
                      aria-label={`Node ${nodeName(n)}`}
                      onClick={() => setSelected({ kind: 'node', id: n.id })}
                      className={`rounded-md border px-2 py-0.5 ${
                        selected?.kind === 'node' && selected.id === n.id
                          ? 'border-purple-400 bg-purple-900/60 text-white'
                          : 'border-gray-700 bg-gray-900 text-gray-300 hover:text-white'
                      }`}
                    >
                      {nodeName(n)}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h4 className="mb-1 font-semibold text-gray-300">
              Edges <span className="font-normal text-gray-500">({graph.edges.length})</span>
            </h4>
            {graph.edges.length === 0 ? (
              <p className="text-gray-500">No edges were found.</p>
            ) : (
              <ul className="space-y-1">
                {graph.edges.map((e) => (
                  <li key={e.id}>
                    <button
                      type="button"
                      aria-pressed={selected?.kind === 'edge' && selected.id === e.id}
                      aria-label={`Edge ${edgeName(graph, e)}`}
                      onClick={() => setSelected({ kind: 'edge', id: e.id })}
                      className={`w-full rounded-md border px-2 py-0.5 text-left ${
                        selected?.kind === 'edge' && selected.id === e.id
                          ? 'border-purple-400 bg-purple-900/60 text-white'
                          : 'border-gray-700 bg-gray-900 text-gray-300 hover:text-white'
                      }`}
                    >
                      {edgeName(graph, e)}
                      {(e.source === null || e.target === null) && (
                        <span className="ml-1 text-amber-300">(loose end)</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {(node || edge) && (
            <SelectionTools
              key={selectedKey}
              node={node}
              edge={edge}
              disabled={readOnly || busy}
              send={send}
            />
          )}

          <fieldset
            disabled={readOnly || busy}
            className="space-y-2 rounded-xl border border-gray-800 bg-gray-900/60 p-3"
          >
            <legend className="px-1 font-semibold text-gray-300">Add a node</legend>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-gray-300">
                New node shape
                <select
                  className={`${compactInputClass} mt-1`}
                  value={newShape}
                  onChange={(e) => setNewShape(e.target.value as ShapeName)}
                >
                  {SHAPES.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-gray-300">
                New node label
                <input
                  className={`${compactInputClass} mt-1`}
                  value={newLabel}
                  maxLength={200}
                  onChange={(e) => setNewLabel(e.target.value)}
                />
              </label>
            </div>
            <div className="flex flex-wrap gap-2">
              <SmallButton
                icon="fa-solid fa-hand-pointer"
                onClick={() => setPlacing({ shape: newShape, label: newLabel })}
              >
                Place on drawing
              </SmallButton>
              <SmallButton
                icon="fa-solid fa-plus"
                onClick={() =>
                  void send([
                    {
                      op: 'add_node',
                      shape: newShape,
                      label: newLabel,
                      box: placedBox(view, view.x + view.width / 2, view.y + view.height / 2),
                    },
                  ]).then((ok) => ok && setNewLabel(''))
                }
              >
                Add at centre
              </SmallButton>
            </div>
          </fieldset>

          <fieldset
            disabled={readOnly || busy || graph.nodes.length < 2}
            className="space-y-2 rounded-xl border border-gray-800 bg-gray-900/60 p-3"
          >
            <legend className="px-1 font-semibold text-gray-300">Add an edge</legend>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-gray-300">
                Edge from
                <select
                  className={`${compactInputClass} mt-1`}
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                >
                  <option value="">Choose…</option>
                  {graph.nodes.map((n) => (
                    <option key={n.id} value={n.id}>
                      {nodeName(n)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-gray-300">
                Edge to
                <select
                  className={`${compactInputClass} mt-1`}
                  value={to}
                  onChange={(e) => setTo(e.target.value)}
                >
                  <option value="">Choose…</option>
                  {graph.nodes.map((n) => (
                    <option key={n.id} value={n.id}>
                      {nodeName(n)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="block text-gray-300">
              New edge label
              <input
                className={`${compactInputClass} mt-1`}
                value={edgeLabel}
                maxLength={200}
                onChange={(e) => setEdgeLabel(e.target.value)}
              />
            </label>
            <SmallButton
              icon="fa-solid fa-arrow-right"
              disabled={!from || !to || from === to}
              onClick={() =>
                void send([
                  { op: 'add_edge', source: from, target: to, label: edgeLabel, directed: true },
                ]).then((ok) => {
                  if (ok) {
                    setFrom('')
                    setTo('')
                    setEdgeLabel('')
                  }
                })
              }
            >
              Add edge
            </SmallButton>
          </fieldset>
        </div>
      </div>
      {graph.free_labels.length > 0 && (
        <p className="text-[11px] text-gray-400">
          Text found outside any shape: {graph.free_labels.map((l) => l.text).join(' · ')}
        </p>
      )}
      {graph.edited_by_teacher && (
        <p className="text-[11px] text-cyan-300">
          <i className="fa-solid fa-user-pen mr-1" aria-hidden="true" />
          Corrected by a teacher.
        </p>
      )}
    </section>
  )
}
