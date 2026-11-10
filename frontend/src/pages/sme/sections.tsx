import { Link } from 'react-router'
import ComingSoonPage from '../admin/ComingSoonPage'

const older =
  'inline-flex items-center gap-2 rounded-xl border border-gray-700 bg-gray-800 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-gray-700'

/** Sections of the SME portal a later prompt builds (P35–P36): a banner and a short empty
 * state, with the older screen that does the job meanwhile (D215). */
export function ProfilePage() {
  return (
    <ComingSoonPage
      icon="fa-solid fa-user"
      iconTone="text-emerald-400"
      title="My Profile"
      description="Your name, email, department and the subjects you are granted."
      emptyTitle="Your profile page comes later"
      emptyText="Meanwhile, your profile, password and recovery codes are in the account menu at the top right."
    />
  )
}

export function ReportsPage() {
  return (
    <ComingSoonPage
      icon="fa-solid fa-file-lines"
      iconTone="text-indigo-400"
      title="My Reports"
      description="Your turnaround, on-time rate and work per quarter."
      emptyTitle="Your reports come later"
      emptyText="The figures on My Assignments are the start: completion rate, units by state and average turnaround."
      action={
        <Link to="/sme" className={older}>
          Back to My Assignments
        </Link>
      }
    />
  )
}
