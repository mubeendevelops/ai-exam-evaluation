import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { PageBanner } from '../components/app/PageBanner'
import { QuestionDetailView } from '../components/qna/QuestionDetailView'
import { QuestionList } from '../components/qna/QuestionList'
import { QuestionModal } from '../components/qna/QuestionModal'
import { SmallButton } from '../components/schema/controls'
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
        <SmallButton icon="fa-solid fa-plus" tone="purple" onClick={() => setCreating(true)}>
          New Question
        </SmallButton>
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
