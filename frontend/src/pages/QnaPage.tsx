import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { PageBanner } from '../components/app/PageBanner'
import { QuestionDetailView } from '../components/qna/QuestionDetailView'
import { QuestionList } from '../components/qna/QuestionList'
import { QuestionModal } from '../components/qna/QuestionModal'
import { useToast } from '../components/ui'

/**
 * `/qna`: the question bank. `?question=<id>` opens one question (so the browser's Back button
 * returns to the list); without it, the repository browser.
 */
export default function QnaPage() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const [creating, setCreating] = useState(false)
  const openId = params.get('question')

  const open = (id: string) => setParams({ question: id })
  const back = () => setParams({})

  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-layer-group"
        iconTone="text-purple-400"
        title="Questions & Answers Repository"
        description="Manage institutional question banks, benchmark answers, rubrics and reference diagrams."
      >
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-purple-900/30 transition hover:from-purple-500 hover:to-indigo-500"
        >
          <i className="fa-solid fa-plus" aria-hidden="true" /> New Question
        </button>
      </PageBanner>

      {openId ? (
        <QuestionDetailView key={openId} id={openId} onBack={back} onOpen={open} />
      ) : (
        <QuestionList onOpen={open} onNew={() => setCreating(true)} />
      )}

      <QuestionModal
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={(id) => {
          setCreating(false)
          toast.show('Question saved.')
          open(id)
        }}
      />
    </div>
  )
}
