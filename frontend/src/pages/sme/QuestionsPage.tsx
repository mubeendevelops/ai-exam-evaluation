import { Link, useNavigate } from 'react-router'
import { QuestionList } from '../../components/qna/QuestionList'
import { GlassPanel } from '../../components/ui'
import { editorUrl } from '../../lib/editorLinks'
import { PrimaryButton } from '../admin/controls'

/**
 * Questions (`/sme/questions`, P35): the search and list of the questions of the SME's subjects,
 * paged, each opening the editor; New question starts a blank one (D165: the bank is a search
 * used while creating questions, not a tab of its own, so this page is reached from Manage
 * Question Papers).
 */
export default function QuestionsPage() {
  const navigate = useNavigate()
  const create = () => navigate(editorUrl(null))
  return (
    <div className="space-y-6">
      <h1 className="sr-only">Questions</h1>
      <GlassPanel as="section" aria-labelledby="questions-heading" className="space-y-2 p-6">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div>
            <h2 id="questions-heading" className="text-base font-semibold text-white">
              Questions
            </h2>
            <p className="mt-1 text-xs text-gray-400">
              The questions of your subjects, with their keys and rubrics.{' '}
              <Link to="/sme/papers" className="text-cyan-300 hover:underline">
                Back to Manage Question Papers
              </Link>
            </p>
          </div>
          <PrimaryButton onClick={create}>
            <i className="fa-solid fa-plus" aria-hidden="true" /> New question
          </PrimaryButton>
        </div>
      </GlassPanel>
      <QuestionList onOpen={(id) => navigate(editorUrl(id))} onNew={create} />
    </div>
  )
}
