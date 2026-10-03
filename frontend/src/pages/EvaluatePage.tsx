import { PageBanner } from '../components/app/PageBanner'
import { EmptyState, Stepper } from '../components/ui'

export const evaluationSteps = ['Scan Upload', 'AI Segmentation', 'Evaluation View'] as const

/** `/evaluate`: placeholder with the prototype's banner and its three-step progress bar. */
export default function EvaluatePage() {
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-microchip"
        iconTone="text-cyan-400"
        title="AI Automated Answer Evaluation Pipeline"
        description="Bulk uploaded handwritten answer sheets undergo instant AI question segmentation, keyword mapping & rubric grading."
      >
        <Stepper steps={evaluationSteps} current={0} label="Evaluation steps" />
      </PageBanner>
      <EmptyState icon="fa-solid fa-cloud-arrow-up" title="Evaluation is coming soon">
        Upload photographed answer booklets, check how they were split into answers, then review the
        suggested marks and decide every mark yourself.
      </EmptyState>
    </div>
  )
}
