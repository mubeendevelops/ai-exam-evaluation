import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'
import { PageBanner } from '../components/app/PageBanner'
import { EvaluationStep } from '../components/evaluate/EvaluationStep'
import { useBookletDetail } from '../components/evaluate/queries'
import { SegmentationStep } from '../components/evaluate/SegmentationStep'
import { Stepper } from '../components/ui'
import { questionListPath } from './sme/StudentListPage'

export const evaluationSteps = ['AI Segmentation', 'Evaluation View'] as const

/**
 * `/evaluate`: the evaluation screens of one booklet inside the SME frame. The address says
 * where you are, so Back and a reload keep the place: `?booklet=<id>` (AI Segmentation) and
 * `?booklet=<id>&step=3[&answer=<label>][&view=summary]` (Evaluation View). `&from=` carries
 * the student list's filter for the breadcrumb. The SME reaches a booklet from a unit's student
 * and question lists (P32, D217); without a booklet the page sends them to My Assignments.
 */
export default function EvaluatePage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const booklet = params.get('booklet')
  const from = params.get('from') ?? ''
  const step: 0 | 1 = params.get('step') === '3' ? 1 : 0
  const detail = useBookletDetail(booklet ?? '')
  if (!booklet) return <Navigate to="/sme" replace />

  const keep: Record<string, string> = from ? { from } : {}
  const toSegments = () => setParams({ booklet, ...keep })
  const toEvaluation = () => setParams({ booklet, step: '3', ...keep })
  const unit = detail.data?.unit
  const students = unit ? `/sme/units/${unit.id}${from}` : '/sme'
  const questions = unit ? questionListPath(unit.id, booklet, from) : '/sme'
  const label = step === 1 ? params.get('answer') : null

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-xs text-gray-400">
        <Link to="/sme" className="hover:text-white">
          My Assignments
        </Link>
        {unit && detail.data && (
          <>
            {' › '}
            <Link to={students} className="hover:text-white">
              {unit.code}
            </Link>
            {' › '}
            <Link to={questions} className="hover:text-white">
              {detail.data.student.name}
            </Link>
          </>
        )}
        {' › '}
        <span className="text-gray-200">
          {step === 0 ? 'Pages and segments' : label ? `Q${label}` : 'Evaluation'}
        </span>
      </nav>
      <PageBanner
        icon="fa-solid fa-microchip"
        iconTone="text-cyan-400"
        title="AI Automated Answer Evaluation Pipeline"
        description="Handwritten answer sheets are split into answers by the AI, mapped to the key and graded against the rubric; you decide every mark."
      >
        <Stepper
          steps={evaluationSteps}
          current={step}
          onSelect={(index) => (index === 0 ? toSegments() : toEvaluation())}
          label="Evaluation steps"
        />
      </PageBanner>

      {step === 0 && (
        <SegmentationStep
          key={booklet}
          bookletId={booklet}
          onBack={() => void navigate(questions)}
          onProceed={toEvaluation}
        />
      )}
      {step === 1 && (
        <EvaluationStep
          key={booklet}
          bookletId={booklet}
          onBack={toSegments}
          onStudents={() => void navigate(students)}
        />
      )}
    </div>
  )
}
