import { PageBanner } from '../components/app/PageBanner'
import { EmptyState } from '../components/ui'

/** `/schema`: placeholder with the prototype's banner. The exam blueprint designer comes later. */
export default function SchemaPage() {
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-file-signature"
        iconTone="text-purple-400"
        title="Question Paper Studio & Schema Engine"
        description="Design mark blueprints (e.g. 50-mark, 100-mark) and save custom named schemas."
      />
      <EmptyState icon="fa-solid fa-sliders" title="The schema designer is coming soon">
        Define sections, choice rules and mark totals for an exam, and save the blueprint to use it
        when you register booklets.
      </EmptyState>
    </div>
  )
}
