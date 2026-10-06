import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import type { GraphEdit } from '../../lib/graph'
import { GraphEditor } from '../graph/GraphEditor'
import { SelectInput, SmallButton } from '../schema/controls'
import { Badge, Modal, useToast } from '../ui'

type Diagram = components['schemas']['ReferenceDiagramOut']
type Kind = Diagram['kind']

const KINDS: { value: Kind; label: string; compared: boolean }[] = [
  { value: 'flowchart', label: 'Flowchart', compared: true },
  { value: 'block', label: 'Block diagram', compared: true },
  { value: 'network', label: 'Network', compared: true },
  { value: 'tree', label: 'Tree', compared: true },
  { value: 'circuit', label: 'Circuit', compared: false },
  { value: 'plot', label: 'Plot', compared: false },
  { value: 'labelled_drawing', label: 'Labelled drawing', compared: false },
]

const RECOGNITION: Record<Diagram['recognition'], string> = {
  pending: 'Waiting to be read',
  recognised: 'Read by the recognizer',
  failed: 'Could not be read: draw it here',
  edited: 'Corrected by a teacher',
}

/**
 * The graph of a reference diagram, drawn over its picture and editable by the owning college
 * (each save is the diagram's next version; scores already made keep the version they used).
 */
export function ReferenceGraphModal({
  open,
  onClose,
  questionId,
  diagram,
  editable,
}: {
  open: boolean
  onClose: () => void
  questionId: string
  diagram: Diagram
  editable: boolean
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [reading, setReading] = useState(false)
  const key = ['reference-graph', questionId, diagram.id]

  const graph = useQuery({
    queryKey: key,
    enabled: open,
    queryFn: async () => {
      const { data } = await api.GET(
        '/api/v1/questions/{question_id}/diagrams/{diagram_id}/graph',
        {
          params: { path: { question_id: questionId, diagram_id: diagram.id } },
        },
      )
      if (!data) throw new Error('graph unavailable')
      return data
    },
    // The worker reads a fresh upload in the background.
    refetchInterval: (query) => (query.state.data?.recognition === 'pending' ? 3000 : false),
  })
  const current = graph.data

  async function save(edits: GraphEdit[], kind?: Kind): Promise<boolean> {
    if (!current) return false
    try {
      const { data, error } = await api.POST(
        '/api/v1/questions/{question_id}/diagrams/{diagram_id}/graph/edits',
        {
          params: { path: { question_id: questionId, diagram_id: diagram.id } },
          body: { expected_version: current.version, edits, kind: kind ?? null },
        },
      )
      if (!data) {
        toast.show(problemOf(error, 'Could not save the change.').message, 'red')
        await graph.refetch()
        return false
      }
      queryClient.setQueryData(key, data)
      await queryClient.invalidateQueries({ queryKey: ['question', questionId] })
      return true
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
      return false
    }
  }

  async function readAgain() {
    setReading(true)
    try {
      const { response, error } = await api.POST(
        '/api/v1/questions/{question_id}/diagrams/{diagram_id}/recognize',
        { params: { path: { question_id: questionId, diagram_id: diagram.id } } },
      )
      if (response.ok) {
        toast.show('The picture is being read again. The result becomes the next version.')
        await graph.refetch()
      } else {
        toast.show(problemOf(error, 'Could not queue the reading.').message, 'red')
      }
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
    } finally {
      setReading(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="max-w-5xl"
      eyebrow="Reference diagram"
      title={diagram.name}
      description="Nodes, edges and labels are what student drawings are compared with."
    >
      {graph.isError && <p className="text-xs text-red-400">The graph could not be loaded.</p>}
      {!current && !graph.isError && <p className="text-xs text-gray-400">Loading…</p>}
      {current && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge tone={current.recognition === 'failed' ? 'red' : 'cyan'}>
              {RECOGNITION[current.recognition]}
            </Badge>
            <Badge tone="gray">Version {current.version}</Badge>
            {editable ? (
              <>
                <SelectInput
                  label="Diagram kind"
                  value={current.kind}
                  onChange={(e) => void save([], e.target.value as Kind)}
                  hint={
                    KINDS.find((k) => k.value === current.kind)?.compared === false
                      ? 'Not compared yet: its criteria stay with the teacher.'
                      : undefined
                  }
                >
                  {KINDS.map((k) => (
                    <option key={k.value} value={k.value}>
                      {k.label}
                    </option>
                  ))}
                </SelectInput>
                <SmallButton
                  icon="fa-solid fa-rotate"
                  tone="gray"
                  disabled={reading}
                  onClick={() => void readAgain()}
                >
                  Read the picture again
                </SmallButton>
              </>
            ) : (
              <span className="text-gray-400">Only the owning college edits this diagram.</span>
            )}
          </div>
          <GraphEditor
            title={`Graph of ${diagram.name}`}
            imagePath={current.content_url}
            graph={current.graph}
            readOnlyReason={
              editable
                ? undefined
                : 'This diagram belongs to another college: copy the question to edit it.'
            }
            onEdit={(edits) => save(edits)}
          />
        </div>
      )}
    </Modal>
  )
}
