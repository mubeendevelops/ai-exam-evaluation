import { useMe } from '../../auth/AuthProvider'
import { roleLabel } from '../../auth/capabilities'
import { Badge, Modal } from '../ui'
import { ChangePasswordForm } from './ChangePasswordForm'

/** Who am I, which college, and change password (the admin's and the Governor's account menu;
 * the SME has the My Profile page, P36). */
export function ProfileModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useMe()
  const { user } = me
  const rows: [string, string][] = [
    ['Name', user.display_name],
    ['Email', user.email],
    ['College', me.college_name],
    ['Institution ID', me.institution_id],
  ]
  return (
    <Modal open={open} onClose={onClose} eyebrow="Account" title="Profile">
      <dl className="mb-6 space-y-2 text-xs">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 border-b border-gray-800 pb-2">
            <dt className="text-gray-400">{label}</dt>
            <dd className="truncate font-semibold text-white">{value}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-4 border-b border-gray-800 pb-2">
          <dt className="text-gray-400">Role</dt>
          <dd>
            <Badge tone={user.role === 'admin' ? 'amber' : 'purple'}>{roleLabel[user.role]}</Badge>
          </dd>
        </div>
      </dl>
      <ChangePasswordForm onChanged={onClose} />
    </Modal>
  )
}
