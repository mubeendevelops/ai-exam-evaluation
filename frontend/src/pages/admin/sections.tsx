import { Link } from 'react-router'
import ComingSoonPage from './ComingSoonPage'

export function OverviewPage() {
  return (
    <ComingSoonPage
      icon="fa-solid fa-gauge-high"
      iconTone="text-purple-400"
      title="Dashboard Overview"
      description="How the college's evaluation is going, at a glance."
      emptyTitle="Nothing to show yet"
      emptyText="Figures appear here once papers are uploaded and assigned to SMEs. Start with the SME Directory & Subjects."
    />
  )
}

export function UploadAssignPage() {
  return (
    <ComingSoonPage
      icon="fa-solid fa-file-arrow-up"
      iconTone="text-cyan-400"
      title="Upload & Assign Papers"
      description="Upload answer booklets and give them to the SME of their subject."
      emptyTitle="Uploading and assigning is not built yet"
      emptyText="Papers will be uploaded and assigned here. Meanwhile the student roster they are filed under is ready."
      action={
        <Link
          to="/admin/upload/students"
          className="inline-flex items-center gap-2 rounded-xl border border-purple-500/40 bg-purple-950/50 px-4 py-2 text-xs font-bold text-purple-200 hover:bg-purple-900/60"
        >
          <i className="fa-solid fa-user-graduate" aria-hidden="true" /> Open the student roster
        </Link>
      }
    />
  )
}

export function ProgressPage() {
  return (
    <ComingSoonPage
      icon="fa-solid fa-chart-line"
      iconTone="text-emerald-400"
      title="SME Progress Reports"
      description="How far each SME has got with their assigned papers."
      emptyTitle="No progress to report yet"
      emptyText="Progress per SME and subject appears here once papers are assigned."
    />
  )
}

export function RedistributePage() {
  return (
    <ComingSoonPage
      icon="fa-solid fa-arrow-right-arrow-left"
      iconTone="text-amber-400"
      title="Redistribute Workload"
      description="Move papers from one SME to another entitled to the subject."
      emptyTitle="Nothing to redistribute yet"
      emptyText="Once papers are assigned you can move them between SMEs here."
    />
  )
}

export function ReportsPage() {
  return (
    <ComingSoonPage
      icon="fa-solid fa-file-circle-check"
      iconTone="text-indigo-400"
      title="Audit & External Reports"
      description="Reports for the college's external bodies and the audit trail."
      emptyTitle="No reports yet"
      emptyText="Reports are built on request and appear here when they are ready."
    />
  )
}
