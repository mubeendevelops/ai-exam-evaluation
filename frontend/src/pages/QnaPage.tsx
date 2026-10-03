import { PageBanner } from '../components/app/PageBanner'
import { EmptyState } from '../components/ui'

/** `/qna`: placeholder with the prototype's banner. The question bank itself comes later. */
export default function QnaPage() {
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-layer-group"
        iconTone="text-purple-400"
        title="Questions & Answers Repository"
        description="Manage institutional question banks, mark-specific answers and references."
      />
      <EmptyState icon="fa-solid fa-database" title="The question bank is coming soon">
        Browse questions by subject, add reference answers, rubrics and glossaries, and copy another
        college's questions into your own.
      </EmptyState>
    </div>
  )
}
