import { useSearchParams } from 'react-router'
import { PageBanner } from '../components/app/PageBanner'
import { EvaluationStep } from '../components/evaluate/EvaluationStep'
import { SegmentationStep } from '../components/evaluate/SegmentationStep'
import { UploadStep } from '../components/evaluate/UploadStep'
import { EmptyState, Stepper } from '../components/ui'
import { SmallButton } from '../components/schema/controls'

export const evaluationSteps = ['Scan Upload', 'AI Segmentation', 'Evaluation View'] as const

/**
 * `/evaluate`: the three steps of the prototype's evaluation tab. The address says where you
 * are, so Back and a reload keep the place: `/evaluate` (1, Scan Upload),
 * `/evaluate?booklet=<id>` (2, AI Segmentation) and `?booklet=<id>&step=3` (3, Evaluation View).
 */
export default function EvaluatePage() {
  const [params, setParams] = useSearchParams()
  const booklet = params.get('booklet')
  const step: 0 | 1 | 2 =
    params.get('step') === '3' ? 2 : booklet || params.get('step') === '2' ? 1 : 0

  const toUploads = () => setParams({})
  const toSegments = (id: string) => setParams({ booklet: id })
  const toEvaluation = (id: string) => setParams({ booklet: id, step: '3' })

  const select = (index: number) => {
    if (index === 0) toUploads()
    else if (index === 1) setParams(booklet ? { booklet } : { step: '2' })
    else if (booklet) toEvaluation(booklet)
    else setParams({ step: '3' })
  }

  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-microchip"
        iconTone="text-cyan-400"
        title="AI Automated Answer Evaluation Pipeline"
        description="Bulk uploaded handwritten answer sheets undergo instant AI question segmentation, keyword mapping & rubric grading."
      >
        <Stepper
          steps={evaluationSteps}
          current={step}
          onSelect={select}
          label="Evaluation steps"
        />
      </PageBanner>

      {step === 0 && <UploadStep onOpen={toSegments} />}
      {step === 1 &&
        (booklet ? (
          <SegmentationStep
            key={booklet}
            bookletId={booklet}
            onBack={toUploads}
            onProceed={() => toEvaluation(booklet)}
          />
        ) : (
          <ChooseBooklet onBack={toUploads} what="see how it was split into answers" />
        ))}
      {step === 2 &&
        (booklet ? (
          <EvaluationStep
            key={booklet}
            bookletId={booklet}
            onBack={() => toSegments(booklet)}
            onSubmissions={toUploads}
          />
        ) : (
          <ChooseBooklet onBack={toUploads} what="decide its marks" />
        ))}
    </div>
  )
}

function ChooseBooklet({ onBack, what }: { onBack: () => void; what: string }) {
  return (
    <EmptyState
      icon="fa-solid fa-file-circle-question"
      title="Choose a booklet first"
      action={
        <SmallButton icon="fa-solid fa-arrow-left" onClick={onBack}>
          Go to the submissions
        </SmallButton>
      }
    >
      Press Run AI Eval or Open on one of the uploaded submissions to {what}.
    </EmptyState>
  )
}
