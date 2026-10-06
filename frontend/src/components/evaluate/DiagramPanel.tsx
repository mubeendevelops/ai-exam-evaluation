import { api } from '../../api/client'
import type { GraphEdit } from '../../lib/graph'
import { asRect, indexRegions, type PageText, type SegmentView } from '../../lib/segments'
import { GraphEditor } from '../graph/GraphEditor'
import { GlassPanel } from '../ui'
import type { BookletDetail } from './model'
import type { StudentDiagram } from './queries'
import type { useGuardedEdit } from './useEdits'

/**
 * The drawings found in the booklet, each over its crop of the page with the graph the
 * recognizer built from it. A correction is one call; the answer is then scored again.
 */
export function DiagramPanel({
  booklet,
  diagrams,
  views,
  texts,
  readOnlyReason,
  guarded,
}: {
  booklet: BookletDetail
  diagrams: StudentDiagram[]
  views: SegmentView[]
  texts: Map<number, PageText>
  readOnlyReason: string | undefined
  guarded: ReturnType<typeof useGuardedEdit>
}) {
  if (diagrams.length === 0) return null
  const regions = indexRegions(texts)
  return (
    <GlassPanel as="section" aria-label="Diagrams" className="space-y-6 p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
        <i className="fa-solid fa-diagram-project text-purple-400" aria-hidden="true" />
        Diagrams in the answers
      </h3>
      {diagrams.map((d) => {
        const owner = views.find((v) => v.segment.id === d.segment_id)
        const number = (d.region_id ? regions.get(d.region_id)?.page : undefined) ?? owner?.pages[0]
        const page = booklet.pages.find((p) => p.number === number)
        const title = `Diagram in ${owner?.label ? `question ${owner.label}` : 'an unassigned segment'}`
        if (!page?.image_url) {
          return (
            <p key={d.id} className="text-xs text-gray-400">
              {title}: its page is not available.
            </p>
          )
        }
        const send = async (edits: GraphEdit[]) => {
          const done = await guarded.run(
            () =>
              api.POST('/api/v1/booklets/{booklet_id}/diagrams/{diagram_id}/edits', {
                params: { path: { booklet_id: booklet.id, diagram_id: d.id } },
                body: { expected_version: d.version, edits },
              }),
            'Could not save the change to the diagram.',
          )
          return done !== undefined
        }
        return (
          <GraphEditor
            key={d.id}
            title={title}
            imagePath={page.image_url}
            imageSize={{ width: page.width, height: page.height }}
            crop={asRect(d.box)}
            graph={d.graph}
            readOnlyReason={readOnlyReason}
            onEdit={send}
          />
        )
      })}
    </GlassPanel>
  )
}
