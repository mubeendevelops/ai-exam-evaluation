import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { fetchBlob, saveBlob } from '../../api/files'
import type { components } from '../../api/schema'
import { Badge, EmptyState, GlassPanel, useToast } from '../ui'
import { SmallButton } from '../schema/controls'
import { AuthImage } from './AuthImage'
import { DifficultyPill } from './QuestionList'
import { QuestionModal } from './QuestionModal'
import { AnswerModal, GlossaryModal, RubricModal } from './SmallModals'
import { UploadDiagramModal, UploadKeyModal } from './UploadModals'

type Question = components['schemas']['QuestionOut']
type Answer = components['schemas']['ReferenceAnswerOut']
type Criterion = components['schemas']['CriterionOut']

type Dialog = 'edit' | 'rubric' | 'answer' | 'glossary' | 'key' | 'diagram' | null

function describe(c: Criterion['criterion'], diagrams: Question['diagrams']): string {
  switch (c.type) {
    case 'list':
      return `${c.params.items.length} items, ${c.params.required_count} required`
    case 'numeric':
      return `${c.params.expected} ± ${c.params.tolerance ?? 0}${c.params.unit ? ` ${c.params.unit}` : ''}`
    case 'semantic':
      return c.params.reference_statement
    case 'diagram': {
      const name = diagrams.find((d) => d.id === c.params.reference_diagram_id)?.name
      return `${c.params.component ?? 'whole'} of ${name ?? 'a reference diagram'}`
    }
  }
}

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

/** One question: benchmark keys, rubric, glossary and attached files, with Edit or Copy. */
export function QuestionDetailView({
  id,
  onBack,
  onOpen,
}: {
  id: string
  onBack: () => void
  onOpen: (id: string) => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [dialog, setDialog] = useState<Dialog>(null)
  const [answer, setAnswer] = useState<Answer | undefined>()

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

  const copy = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST('/api/v1/questions/{question_id}/copy', {
        params: { path: { question_id: id } },
      })
      if (!data) throw new Error(problemOf(error, 'Could not copy the question.').message)
      return data
    },
    onSuccess: async (copied) => {
      await queryClient.invalidateQueries({ queryKey: ['questions'] })
      toast.show(`Copied to your college as ${copied.code}.`)
      onOpen(copied.id)
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : NETWORK_PROBLEM, 'red'),
  })

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
      Back to Repository Browser
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

  const rubric = q.rubric
  const own = q.owned
  const close = () => {
    setDialog(null)
    setAnswer(undefined)
  }

  return (
    <div className="space-y-6">
      <GlassPanel className="flex flex-wrap items-center justify-between gap-3 p-4">
        {back}
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to="/evaluate"
            className="inline-flex items-center gap-1.5 rounded-xl border border-gray-700 bg-gray-800 px-4 py-1.5 text-xs font-semibold text-gray-200 hover:bg-gray-700"
          >
            <i className="fa-solid fa-flask-vial text-purple-400" aria-hidden="true" />
            Test in AI Evaluator
          </Link>
          {own ? (
            <>
              <SmallButton icon="fa-solid fa-pen" tone="purple" onClick={() => setDialog('edit')}>
                Edit question
              </SmallButton>
              <SmallButton icon="fa-solid fa-cloud-arrow-up" onClick={() => setDialog('key')}>
                Upload Answer Key
              </SmallButton>
            </>
          ) : (
            <SmallButton
              icon="fa-solid fa-copy"
              tone="purple"
              disabled={copy.isPending}
              onClick={() => copy.mutate()}
            >
              Copy to my college
            </SmallButton>
          )}
        </div>
      </GlassPanel>

      {!own && (
        <p
          role="note"
          className="rounded-xl border border-purple-500/30 bg-purple-950/30 px-4 py-3 text-xs text-purple-100"
        >
          Owned by <strong>{q.owner_name ?? 'another college'}</strong>. Only its teachers edit it.
          Copy it to your college to change the wording, keys or rubric.
        </p>
      )}

      <GlassPanel className="space-y-3 p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded border border-purple-500/30 bg-purple-950 px-2.5 py-1 font-mono text-xs font-bold text-purple-300">
              {q.code}
            </span>
            <span className="rounded bg-gray-800 px-2.5 py-1 text-xs text-gray-300">
              {q.subject_name ?? 'No subject'}
            </span>
            {q.category && (
              <span className="rounded bg-gray-800 px-2.5 py-1 text-xs text-cyan-300">
                {q.category}
              </span>
            )}
            <span className="rounded-full border border-purple-500/30 bg-purple-950/80 px-2.5 py-1 text-xs font-bold text-purple-300">
              {q.max_marks} {q.max_marks === 1 ? 'Mark' : 'Marks'}
            </span>
          </div>
          <DifficultyPill level={q.difficulty} />
        </div>
        <h2 className="text-xl leading-snug font-bold whitespace-pre-line text-white">{q.text}</h2>
        <p className="text-[11px] text-gray-400">
          {own ? 'Owned by your college' : `Owned by ${q.owner_name ?? 'another college'}`} ·
          version {q.version}
          {q.copied_from && ' · copied from another question'}
        </p>
      </GlassPanel>

      <GlassPanel as="section" aria-label="Benchmark answers" className="space-y-4 p-6">
        <SectionTitle
          icon="fa-solid fa-list-check"
          tone="text-purple-400"
          action={
            own && (
              <SmallButton
                icon="fa-solid fa-plus"
                onClick={() => {
                  setAnswer(undefined)
                  setDialog('answer')
                }}
              >
                Add reference answer
              </SmallButton>
            )
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
              {a.synthetic && <Badge tone="gray">SYNTHETIC – dev only</Badge>}
              {own && (
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
              )}
            </div>
            <p className="whitespace-pre-line">{a.text}</p>
          </div>
        ))}
      </GlassPanel>

      <GlassPanel as="section" aria-label="Rubric" className="space-y-4 p-6">
        <SectionTitle
          icon="fa-solid fa-scale-balanced"
          tone="text-cyan-400"
          action={
            own && (
              <SmallButton icon="fa-solid fa-pen" onClick={() => setDialog('rubric')}>
                Edit rubric
              </SmallButton>
            )
          }
        >
          Rubric
        </SectionTitle>
        {rubric.criteria.length === 0 ? (
          <p className="text-xs text-gray-400">No criteria: a guidance-only key, marked by hand.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-gray-800 text-[11px] text-gray-400 uppercase">
                  <th className="py-2 pr-3">Criterion</th>
                  <th className="py-2 pr-3">Method</th>
                  <th className="py-2 pr-3">Parameters</th>
                  <th className="py-2 text-right">Marks</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {rubric.criteria.map((c) => (
                  <tr key={c.criterion.id}>
                    <td className="py-2 pr-3 font-medium text-white">{c.criterion.label}</td>
                    <td className="py-2 pr-3">
                      <Badge tone="cyan">{c.criterion.type}</Badge>
                    </td>
                    <td className="max-w-xs truncate py-2 pr-3 text-gray-300">
                      {describe(c.criterion, q.diagrams)}
                    </td>
                    <td className="py-2 text-right font-bold text-emerald-400">
                      {c.criterion.weight}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rubric.criteria.length > 0 && (
          <p
            role="status"
            className={`text-xs ${rubric.complete ? 'text-emerald-400' : 'text-amber-400'}`}
          >
            Weights add up to {rubric.total} of {rubric.max_marks} marks
            {rubric.complete ? '.' : ': the AI cannot score until they match.'}
          </p>
        )}
      </GlassPanel>

      <GlassPanel as="section" aria-label="Glossary" className="space-y-3 p-6">
        <SectionTitle
          icon="fa-solid fa-spell-check"
          tone="text-amber-400"
          action={
            own && (
              <SmallButton icon="fa-solid fa-pen" onClick={() => setDialog('glossary')}>
                Edit glossary
              </SmallButton>
            )
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
            own && (
              <SmallButton icon="fa-solid fa-diagram-project" onClick={() => setDialog('diagram')}>
                Upload reference diagram
              </SmallButton>
            )
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
                  {d.node_count === 0 && (
                    <span className="block text-[11px] text-gray-400">
                      Nodes and edges are read from the picture by the diagram recognizer.
                    </span>
                  )}
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </GlassPanel>

      <QuestionModal
        open={dialog === 'edit'}
        onClose={close}
        question={q}
        onSaved={() => {
          close()
          toast.show('Question saved.')
        }}
      />
      <RubricModal open={dialog === 'rubric'} onClose={close} question={q} />
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
