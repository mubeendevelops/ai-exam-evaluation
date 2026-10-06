import { useId } from 'react'
import { edgeLine, viewOf, type DiagramGraph, type Rect } from '../../lib/graph'
import { refNodeName, studentNodeName, type CompareEdge, type Comparison } from '../../lib/review'
import { useFileUrl } from '../qna/AuthImage'

const COLOUR = { ok: '#059669', extra: '#d97706', reversed: '#dc2626' } as const
type Mark = keyof typeof COLOUR

interface Props {
  title: string
  comparison: Comparison
  /** The student's graph and where it is on its page; absent when no drawing was found. */
  drawing?: {
    graph: DiagramGraph
    imagePath: string
    imageSize: { width: number; height: number }
    crop: Rect
  }
}

function edgeMark(e: CompareEdge): Mark | null {
  if (e.status === 'present') return 'ok'
  if (e.status === 'reversed') return 'reversed'
  if (e.status === 'extra') return 'extra'
  return null
}

function pairName(c: Comparison, pair: [string, string] | null, side: 'ref' | 'student') {
  if (!pair) return ''
  const name = side === 'ref' ? refNodeName : studentNodeName
  return `${name(c, pair[0])} → ${name(c, pair[1])}`
}

/**
 * Panel A's diagram check: the student's drawing with every element coloured by what the
 * comparison with the reference found (green matches, amber is extra, red is reversed), and the
 * reference elements the student left out listed beside it (they have no place on the drawing).
 */
export function DiagramOverlay({ title, comparison, drawing }: Props) {
  const file = useFileUrl(drawing?.imagePath)
  const ids = useId()
  const view = drawing ? viewOf(drawing.crop, drawing.imageSize) : null

  const extraNodes = comparison.nodes.filter((n) => n.matched_ref === null)
  const wrongShape = comparison.nodes.filter(
    (n) => n.matched_ref !== null && n.shape_match === false,
  )
  const missingNodes = comparison.reference_nodes.filter((n) => n.matched_student === null)
  const missingEdges = comparison.edges.filter((e) => e.status === 'missing')
  const reversed = comparison.edges.filter((e) => e.status === 'reversed')
  const extraEdges = comparison.edges.filter((e) => e.status === 'extra')
  const clean =
    extraNodes.length +
      wrongShape.length +
      missingNodes.length +
      missingEdges.length +
      reversed.length +
      extraEdges.length ===
    0

  const marker = view ? view.width / 55 : 10
  const stroke = (mark: Mark) => COLOUR[mark]

  return (
    <section
      aria-label={title}
      className="space-y-2 rounded-xl border border-gray-800 bg-gray-950/60 p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold text-gray-200">{title}</h4>
        {comparison.similarity !== null && (
          <span className="text-[11px] text-gray-400">
            Similarity {Math.round(comparison.similarity * 100)}%
            {comparison.sub_scores &&
              ` (nodes ${Math.round(comparison.sub_scores.nodes * 100)}%, edges ${Math.round(comparison.sub_scores.edges * 100)}%, labels ${Math.round(comparison.sub_scores.labels * 100)}%)`}
          </span>
        )}
      </div>

      {drawing && view && (
        <svg
          role="img"
          aria-label={`${title}: the student's drawing with matched, extra and reversed elements marked`}
          viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
          className="block w-full rounded-lg bg-white"
          style={{ aspectRatio: `${view.width} / ${view.height}` }}
        >
          <defs>
            {(Object.keys(COLOUR) as Mark[]).map((mark) => (
              <marker
                key={mark}
                id={`${ids}-${mark}`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerUnits="userSpaceOnUse"
                markerWidth={marker}
                markerHeight={marker}
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={COLOUR[mark]} />
              </marker>
            ))}
          </defs>
          {file.data && (
            <image
              href={file.data}
              x={0}
              y={0}
              width={drawing.imageSize.width}
              height={drawing.imageSize.height}
              opacity={0.55}
            />
          )}
          {comparison.edges.map((e, k) => {
            const mark = edgeMark(e)
            const graphEdge = drawing.graph.edges.find((g) => g.id === e.student_edge)
            const line = mark && graphEdge ? edgeLine(drawing.graph, graphEdge) : null
            if (!mark || !line) return null
            return (
              <line
                key={`${e.student_edge}-${k}`}
                data-mark={`edge-${e.status}`}
                x1={line.from[0]}
                y1={line.from[1]}
                x2={line.to[0]}
                y2={line.to[1]}
                stroke={stroke(mark)}
                strokeWidth={3}
                vectorEffect="non-scaling-stroke"
                markerEnd={graphEdge?.directed ? `url(#${ids}-${mark})` : undefined}
              />
            )
          })}
          {drawing.graph.nodes.map((n) => {
            const found = comparison.nodes.find((c) => c.id === n.id)
            if (!found || !n.box) return null
            const mark: Mark =
              found.matched_ref === null || found.shape_match === false ? 'extra' : 'ok'
            return (
              <g key={n.id} data-mark={found.matched_ref === null ? 'node-extra' : 'node-matched'}>
                <rect
                  x={n.box[0]}
                  y={n.box[1]}
                  width={(n.box[2] ?? 0) - (n.box[0] ?? 0)}
                  height={(n.box[3] ?? 0) - (n.box[1] ?? 0)}
                  fill="none"
                  stroke={stroke(mark)}
                  strokeWidth={3}
                  strokeDasharray={mark === 'extra' ? '6 4' : undefined}
                  vectorEffect="non-scaling-stroke"
                />
                <title>{`${studentNodeName(comparison, n.id)}: ${found.matched_ref === null ? 'extra' : found.shape_match === false ? 'wrong shape' : 'matches the reference'}`}</title>
              </g>
            )
          })}
        </svg>
      )}

      <ul aria-label="Legend" className="flex flex-wrap gap-3 text-[10px] text-gray-400">
        <li>
          <span className="mr-1 inline-block h-2 w-2 rounded-sm bg-emerald-600" />
          matches the reference
        </li>
        <li>
          <span className="mr-1 inline-block h-2 w-2 rounded-sm bg-amber-600" />
          extra
        </li>
        <li>
          <span className="mr-1 inline-block h-2 w-2 rounded-sm bg-red-600" />
          reversed
        </li>
      </ul>

      {clean ? (
        <p className="text-xs text-emerald-300">
          Every element of the reference is there, none is extra or reversed.
        </p>
      ) : (
        <dl className="space-y-1 text-xs">
          {missingNodes.length + missingEdges.length > 0 && (
            <div>
              <dt className="font-semibold text-red-300">Missing</dt>
              <dd className="text-gray-300">
                {[
                  ...missingNodes.map((n) => `box “${refNodeName(comparison, n.id)}”`),
                  ...missingEdges.map((e) => `arrow ${pairName(comparison, e.ref, 'ref')}`),
                ].join('; ')}
              </dd>
            </div>
          )}
          {extraNodes.length + extraEdges.length > 0 && (
            <div>
              <dt className="font-semibold text-amber-300">Extra</dt>
              <dd className="text-gray-300">
                {[
                  ...extraNodes.map((n) => `box “${studentNodeName(comparison, n.id)}”`),
                  ...extraEdges.map((e) => `arrow ${pairName(comparison, e.student, 'student')}`),
                ].join('; ')}
              </dd>
            </div>
          )}
          {reversed.length > 0 && (
            <div>
              <dt className="font-semibold text-red-300">Reversed</dt>
              <dd className="text-gray-300">
                {reversed
                  .map(
                    (e) =>
                      `drawn ${pairName(comparison, e.student, 'student')}, expected ${pairName(comparison, e.ref, 'ref')}`,
                  )
                  .join('; ')}
              </dd>
            </div>
          )}
          {wrongShape.length > 0 && (
            <div>
              <dt className="font-semibold text-amber-300">Different shape</dt>
              <dd className="text-gray-300">
                {wrongShape.map((n) => `“${studentNodeName(comparison, n.id)}”`).join('; ')}
              </dd>
            </div>
          )}
        </dl>
      )}
    </section>
  )
}
