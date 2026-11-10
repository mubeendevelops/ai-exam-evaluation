import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { fetchBlob, saveBlob } from '../../api/files'
import type { components } from '../../api/schema'
import { DeleteContentDialog } from '../app/DeleteContentDialog'
import { Badge, EmptyState, GlassPanel, useToast } from '../ui'
import { SmallButton } from '../schema/controls'
import { AuthImage } from './AuthImage'
import { DifficultyPill } from './QuestionList'
import { QuestionForm } from './QuestionForm'
import { ReferenceGraphModal } from './ReferenceGraphModal'
import { AnswerModal, GlossaryModal } from './SmallModals'
import { UploadDiagramModal, UploadKeyModal } from './UploadModals'

type Answer = components['schemas']['ReferenceAnswerOut']
type RefDiagram = components['schemas']['ReferenceDiagramOut']

type Dialog = 'answer' | 'glossary' | 'key' | 'diagram' | null

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function SectionTitle({
  icon,
  tone,
  children,
  action,
}: {
  icon: string
  tone: string
  children: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className={`flex items-center gap-2 text-sm font-bold tracking-wider uppercase ${tone}`}>
        <i className={icon} aria-hidden="true" /> {children}
      </h3>
      {action}
    </div>
  )
}

/**
 * One question of the college as one page (P35): the question form with its rubric (saving asks
 * first when papers link it, D223), then benchmark answers, glossary and attached files, each
 * with its own dialog. `onBack` leaves the page (also after a permanent delete).
 * SMEs author the content of their subjects (D165, D178); the admin never reaches this page.
 */
export function QuestionEditor({
  id,
  backLabel,
  onBack,
}: {
  id: string
  backLabel: string
  onBack: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [dialog, setDialog] = useState<Dialog>(null)
  const [answer, setAnswer] = useState<Answer | undefined>()
  const [graphOf, setGraphOf] = useState<RefDiagram | null>(null)
  const [deleting, setDeleting] = useState(false)

  const query = useQuery({
    queryKey: ['question', id],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/questions/{question_id}', {
        params: { path: { question_id: id } },
      })
      if (!data) throw new Error('question unavailable')
      return data
    },
    retry: false,
  })
  const q = query.data

  const retire = useMutation({
    mutationFn: async (answerId: string) => {
      const { error, response } = await api.DELETE(
        '/api/v1/questions/{question_id}/reference-answers/{answer_id}',
        { params: { path: { question_id: id, answer_id: answerId } } },
      )
      if (!response.ok) throw new Error(problemOf(error, 'Could not remove the answer.').message)
    },
    onSuccess: async () => {
      toast.show('The reference answer was removed.')
      await queryClient.invalidateQueries({ queryKey: ['question', id] })
      await queryClient.invalidateQueries({ queryKey: ['questions'] })
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : NETWORK_PROBLEM, 'red'),
  })

  async function download(url: string, name: string) {
    try {
      saveBlob(await fetchBlob(url), name)
    } catch {
      toast.show('Could not download the file.', 'red')
    }
  }

  const back = (
    <SmallButton icon="fa-solid fa-arrow-left" tone="gray" onClick={onBack}>
      {backLabel}
    </SmallButton>
  )

  if (query.isPending) return <p className="text-xs text-gray-400">Loading question…</p>
  if (!q) {
    return (
      <div className="space-y-4">
        {back}
        <EmptyState icon="fa-solid fa-circle-question" title="Question not found">
          It may have been removed, or the address is wrong.
        </EmptyState>
      </div>
    )
  }

  const close = () => {
    setDialog(null)
    setAnswer(undefined)
  }

  return (
    <div className="space-y-6">
      <GlassPanel className="flex flex-wrap items-center justify-between gap-3 p-4">
        {back}
        <div className="flex flex-wrap items-center gap-2">
          <SmallButton icon="fa-solid fa-cloud-arrow-up" onClick={() => setDialog('key')}>
            Upload Answer Key
          </SmallButton>
          <SmallButton icon="fa-solid fa-trash" tone="gray" onClick={() => setDeleting(true)}>
            Delete
          </SmallButton>
        </div>
      </GlassPanel>
      {deleting && (
        <DeleteContentDialog
          kind="question"
          id={q.id}
          name={`${q.code}: ${q.text.slice(0, 120)}`}
          onClose={() => setDeleting(false)}
          onDeleted={async () => {
            toast.show(`Question ${q.code} deleted.`)
            queryClient.removeQueries({ queryKey: ['question', id] })
            await queryClient.invalidateQueries({ queryKey: ['questions'] })
            onBack()
          }}
        />
      )}

      <GlassPanel as="section" aria-label="Question" className="space-y-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded border border-purple-500/30 bg-purple-950 px-2.5 py-1 font-mono text-xs font-bold text-purple-300">
              {q.code}
            </span>
            <span className="rounded bg-gray-800 px-2.5 py-1 text-xs text-gray-300">
              {q.subject_name ?? 'No subject'}
            </span>
            <DifficultyPill level={q.difficulty} />
          </div>
          <p className="text-[11px] text-gray-400">Version {q.version}</p>
        </div>
        <QuestionForm
          key={`${q.id}-${q.version}`}
          question={q}
          onSaved={() => toast.show('Question saved.')}
        />
      </GlassPanel>

      <GlassPanel as="section" aria-label="Benchmark answers" className="space-y-4 p-6">
        <SectionTitle
          icon="fa-solid fa-list-check"
          tone="text-purple-400"
          action={
            <SmallButton
              icon="fa-solid fa-plus"
              onClick={() => {
                setAnswer(undefined)
                setDialog('answer')
              }}
            >
              Add reference answer
            </SmallButton>
          }
        >
          Benchmark answer keys
        </SectionTitle>
        {q.reference_answers.length === 0 && (
          <p className="text-xs text-amber-400">
            No reference answer yet: without a key the AI cannot score this question.
          </p>
        )}
        {q.reference_answers.map((a) => (
          <div
            key={a.id}
            className="space-y-2 rounded-xl border border-gray-800 bg-gray-900 p-3.5 text-xs text-gray-200"
          >
            <div className="flex flex-wrap items-center gap-2">
              {a.guidance_only && <Badge tone="amber">Guidance only: mark manually</Badge>}
              {a.synthetic && (
                <Badge tone="purple">SYNTHETIC – dev only, needs teacher validation</Badge>
              )}
              <span className="ml-auto flex gap-2">
                <SmallButton
                  tone="gray"
                  onClick={() => {
                    setAnswer(a)
                    setDialog('answer')
                  }}
                >
                  Edit answer
                </SmallButton>
                <SmallButton
                  tone="gray"
                  disabled={retire.isPending}
                  onClick={() => retire.mutate(a.id)}
                >
                  Remove answer
                </SmallButton>
              </span>
            </div>
            <p className="whitespace-pre-line">{a.text}</p>
          </div>
        ))}
      </GlassPanel>

      <GlassPanel as="section" aria-label="Glossary" className="space-y-3 p-6">
        <SectionTitle
          icon="fa-solid fa-spell-check"
          tone="text-amber-400"
          action={
            <SmallButton icon="fa-solid fa-pen" onClick={() => setDialog('glossary')}>
              Edit glossary
            </SmallButton>
          }
        >
          Glossary
        </SectionTitle>
        {q.glossary.terms.length === 0 ? (
          <p className="text-xs text-gray-400">No terms yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {q.glossary.terms.map((t) => (
              <li key={t}>
                <Badge tone={q.glossary.reference_labels.includes(t) ? 'cyan' : 'purple'}>
                  {t}
                </Badge>
              </li>
            ))}
          </ul>
        )}
        {q.glossary.reference_labels.length > 0 && (
          <p className="text-[11px] text-gray-400">
            Blue terms are labels of the reference diagrams.
          </p>
        )}
      </GlassPanel>

      <GlassPanel as="section" aria-label="Attached files" className="space-y-4 p-6">
        <SectionTitle
          icon="fa-solid fa-paperclip"
          tone="text-emerald-400"
          action={
            <SmallButton icon="fa-solid fa-diagram-project" onClick={() => setDialog('diagram')}>
              Upload reference diagram
            </SmallButton>
          }
        >
          Attached files
        </SectionTitle>
        {q.key_files.length === 0 && q.diagrams.length === 0 && (
          <p className="text-xs text-gray-400">No files attached.</p>
        )}
        <ul className="space-y-2">
          {q.key_files.map((f) => (
            <li
              key={f.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-gray-800 bg-gray-900 p-3 text-xs"
            >
              <span className="flex items-center gap-2 text-gray-200">
                <i
                  className={`fa-solid ${f.media_type === 'application/pdf' ? 'fa-file-pdf text-red-400' : 'fa-file-image text-cyan-400'}`}
                  aria-hidden="true"
                />
                <strong className="text-white">{f.name}</strong>
                <span className="text-gray-400">{formatSize(f.size_bytes)}</span>
                {f.keywords.map((k) => (
                  <Badge key={k} tone="gray">
                    {k}
                  </Badge>
                ))}
              </span>
              <SmallButton
                icon="fa-solid fa-download"
                tone="gray"
                onClick={() => void download(f.content_url, f.name)}
              >
                {`Download ${f.name}`}
              </SmallButton>
            </li>
          ))}
        </ul>
        {q.diagrams.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2">
            {q.diagrams.map((d) => (
              <figure
                key={d.id}
                className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-900 p-3"
              >
                <AuthImage path={d.content_url} alt={`Reference diagram ${d.name}`} />
                <figcaption className="space-y-1 text-xs text-gray-300">
                  <strong className="block text-white">{d.name}</strong>
                  <span>
                    {d.node_count} nodes · {d.edge_count} edges
                  </span>
                  {d.node_count === 0 && d.recognition === 'pending' && (
                    <span className="block text-[11px] text-gray-400">
                      Nodes and edges are read from the picture by the diagram recognizer.
                    </span>
                  )}
                  <SmallButton
                    icon="fa-solid fa-diagram-project"
                    tone="gray"
                    onClick={() => setGraphOf(d)}
                  >
                    {`Edit graph of ${d.name}`}
                  </SmallButton>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </GlassPanel>

      {graphOf && (
        <ReferenceGraphModal
          open
          onClose={() => setGraphOf(null)}
          questionId={q.id}
          diagram={q.diagrams.find((d) => d.id === graphOf.id) ?? graphOf}
          editable
        />
      )}
      <AnswerModal open={dialog === 'answer'} onClose={close} question={q} answer={answer} />
      <GlossaryModal open={dialog === 'glossary'} onClose={close} question={q} />
      <UploadKeyModal
        open={dialog === 'key'}
        onClose={close}
        questionId={q.id}
        questionCode={q.code}
      />
      <UploadDiagramModal
        open={dialog === 'diagram'}
        onClose={close}
        questionId={q.id}
        questionCode={q.code}
      />
    </div>
  )
}
