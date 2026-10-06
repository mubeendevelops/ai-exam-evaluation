import { PageBanner } from '../components/app/PageBanner'
import { EvaluatedList } from '../components/evaluated/EvaluatedList'

/** `/evaluated`: approved booklets with their result sheet PDFs (P18). */
export default function EvaluatedPage() {
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-box-archive"
        iconTone="text-emerald-400"
        title="Evaluated Booklets"
        description="Every approved booklet with its result sheet versions. Search, download any version as a PDF, or delete a booklet."
      />
      <EvaluatedList />
    </div>
  )
}
