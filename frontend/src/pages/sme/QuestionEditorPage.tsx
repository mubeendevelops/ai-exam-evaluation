import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { api } from '../../api/client'
import { QuestionEditor } from '../../components/qna/QuestionEditor'
import { QuestionForm } from '../../components/qna/QuestionForm'
import { SimilarPanel } from '../../components/qna/SimilarPanel'
import { SmallButton } from '../../components/schema/controls'
import { GlassPanel, useToast } from '../../components/ui'
import { editorUrl, returnUrl, safeReturn, type EditorContext } from '../../lib/editorLinks'
import { seedFrom } from '../../lib/questionSeed'

/**
 * The question editor (P35, D165): `/sme/questions/:id` edits a question, `/sme/questions/new`
 * writes one. Beside the form a search of the SME's subjects finds similar questions to open or
 * clone as a start. `?return=` names the page to go back to (a paper); with `?slot=` a new
 * question is linked to that slot on the way back (`?subject=` and `?marks=` start the form
 * with the slot's subject and marks; `?from=` clones a question).
 */
export default function QuestionEditorPage() {
  const { questionId } = useParams()
  const [params] = useSearchParams()
  const back = safeReturn(params.get('return'))
  const context: EditorContext = {
    return: back,
    slot: back ? params.get('slot') : null,
    subject: params.get('subject'),
    marks: params.get('marks'),
    from: params.get('from'),
  }
  return (
    <div className="space-y-6">
      <h1 className="sr-only">{questionId ? 'Edit question' : 'New question'}</h1>
      <div className="grid gap-6 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-8">
          {questionId ? (
            <Saved key={questionId} id={questionId} context={context} />
          ) : (
            <Fresh key={context.from ?? 'blank'} context={context} />
          )}
        </div>
        <div className="lg:col-span-4">
          <div className="lg:sticky lg:top-4">
            <SimilarPanel exclude={questionId} context={context} />
          </div>
        </div>
      </div>
    </div>
  )
}

function backLabel(context: EditorContext): string {
  return context.return ? 'Back to the question paper' : 'Back to Questions'
}

function Saved({ id, context }: { id: string; context: EditorContext }) {
  const navigate = useNavigate()
  return (
    <QuestionEditor
      id={id}
      backLabel={backLabel(context)}
      onBack={() => navigate(context.return ?? '/sme/questions')}
    />
  )
}

function Fresh({ context }: { context: EditorContext }) {
  const navigate = useNavigate()
  const toast = useToast()
  const source = useQuery({
    queryKey: ['question', context.from],
    enabled: !!context.from,
    retry: false,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/questions/{question_id}', {
        params: { path: { question_id: context.from ?? '' } },
      })
      if (!data) throw new Error('question unavailable')
      return data
    },
  })

  if (context.from && source.isPending) {
    return <p className="text-xs text-gray-400">Loading the question to clone…</p>
  }
  const seed = source.data
    ? seedFrom(source.data, context.marks)
    : { subjectId: context.subject ?? '', marks: context.marks ?? '' }

  return (
    <div className="space-y-6">
      <GlassPanel className="flex flex-wrap items-center justify-between gap-3 p-4">
        <SmallButton
          icon="fa-solid fa-arrow-left"
          tone="gray"
          onClick={() => navigate(context.return ?? '/sme/questions')}
        >
          {backLabel(context)}
        </SmallButton>
        {context.slot && (
          <p className="text-xs text-gray-300">
            Saving links the new question to its slot
            {context.marks ? ` (${context.marks} marks)` : ''} on the paper.
          </p>
        )}
      </GlassPanel>
      {context.from && source.isError && (
        <p role="alert" className="text-xs text-amber-300">
          The question to clone was not found: you are starting from a blank form.
        </p>
      )}
      <GlassPanel as="section" aria-label="Question" className="space-y-4 p-6">
        <h2 className="text-base font-semibold text-white">Add New Question</h2>
        <p className="text-xs text-gray-400">
          The question, its benchmark answer and the rubric the AI scores it by. Key files, a
          glossary and reference diagrams can be added once it is saved.
        </p>
        <QuestionForm
          seed={seed}
          onSaved={({ id }) => {
            if (context.return && context.slot) {
              navigate(returnUrl(context.return, { questionId: id, slot: context.slot }))
              return
            }
            toast.show('Question saved.')
            navigate(editorUrl(id, { return: context.return }), { replace: true })
          }}
        />
      </GlassPanel>
    </div>
  )
}
