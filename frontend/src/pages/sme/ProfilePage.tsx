import { useState } from 'react'
import { PageBanner } from '../../components/app/PageBanner'
import { ChangePasswordForm } from '../../components/app/ChangePasswordForm'
import { RecoveryCodesModal } from '../../components/app/RecoveryCodesModal'
import { initialsOf } from '../../components/app/UserMenu'
import { Badge, GlassPanel } from '../../components/ui'
import { useMe } from '../../auth/AuthProvider'
import { useMySubjects } from './data'

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="block text-gray-500">{label}</dt>
      <dd className="mt-0.5 font-medium text-gray-200">{children}</dd>
    </div>
  )
}

const unset = <span className="font-normal text-gray-500">Not set</span>

/**
 * My Profile (P36, D225): what the college holds about the SME, read-only: name, email,
 * employee ID, department and specialisation (the subjects the admin granted). The admin keeps
 * the name, employee ID and department in the SME directory; the password and recovery codes
 * are the SME's own.
 */
export default function ProfilePage() {
  const me = useMe()
  const subjects = useMySubjects()
  const [codes, setCodes] = useState(false)
  const { user } = me
  const parts = [me.college_name, user.employee_id ? `Employee ID: ${user.employee_id}` : null]
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-user"
        iconTone="text-emerald-400"
        title="My Profile"
        description="Your details as your admin keeps them, your password and your recovery codes."
      />

      <GlassPanel
        as="section"
        aria-labelledby="details-heading"
        className="max-w-2xl space-y-5 p-6"
      >
        <div className="flex items-center gap-4">
          <div
            aria-hidden="true"
            className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-xl font-bold text-white ring-4 ring-indigo-500/20"
          >
            {initialsOf(user.display_name)}
          </div>
          <div className="min-w-0">
            <h2 id="details-heading" className="truncate text-lg font-bold text-white">
              {user.display_name}
            </h2>
            <p className="text-xs text-indigo-400">
              Subject Matter Expert{user.department ? ` - ${user.department}` : ''}
            </p>
            <p className="mt-1 text-xs text-gray-500">{parts.filter(Boolean).join(' • ')}</p>
          </div>
        </div>
        <dl className="grid grid-cols-1 gap-4 border-t border-gray-800 pt-4 text-xs sm:grid-cols-2">
          <Detail label="Name">{user.display_name}</Detail>
          <Detail label="Email Address">{user.email}</Detail>
          <Detail label="Employee ID">{user.employee_id ?? unset}</Detail>
          <Detail label="Department">{user.department ?? unset}</Detail>
          <Detail label="Specialization">
            {subjects.isPending && <span className="font-normal text-gray-500">Loading…</span>}
            {subjects.isError && (
              <span role="alert" className="font-normal text-red-300">
                Your subjects could not be loaded.
              </span>
            )}
            {subjects.data && subjects.data.length === 0 && unset}
            {subjects.data && subjects.data.length > 0 && (
              <ul className="flex flex-wrap gap-1" aria-label="Subjects">
                {subjects.data.map((s) => (
                  <li key={s.id}>
                    <Badge tone="purple">{s.name}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Detail>
        </dl>
        <p className="text-[11px] text-gray-500">
          Your admin keeps your name, employee ID, department and subjects. Ask them to correct a
          mistake.
        </p>
      </GlassPanel>

      <div className="grid max-w-5xl gap-6 lg:grid-cols-2">
        <GlassPanel as="section" aria-label="Password" className="p-6">
          <ChangePasswordForm />
        </GlassPanel>
        <GlassPanel as="section" aria-labelledby="codes-heading" className="space-y-3 p-6">
          <h3
            id="codes-heading"
            className="text-sm font-bold tracking-wider text-purple-400 uppercase"
          >
            Recovery codes
          </h3>
          <p className="text-xs text-gray-300">
            One-time codes let you set a new password if you forget it.{' '}
            <strong className="text-white">
              {me.recovery_codes_left} unused code{me.recovery_codes_left === 1 ? '' : 's'}
            </strong>
            . New codes replace the old ones and are shown once.
          </p>
          <button
            type="button"
            onClick={() => setCodes(true)}
            className="inline-flex items-center gap-2 rounded-xl border border-gray-700 bg-gray-800 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-gray-700"
          >
            <i className="fa-solid fa-key" aria-hidden="true" /> Create new recovery codes
          </button>
        </GlassPanel>
      </div>
      <RecoveryCodesModal open={codes} onClose={() => setCodes(false)} />
    </div>
  )
}
