import { useMe } from '../auth/AuthProvider'
import { GlassPanel } from '../components/ui'

/**
 * The Governor signs in to this placeholder until the Governor portal is built (D159, D180):
 * analytics, audits, freeze and sign-off come in later prompts.
 */
export default function GovernorPage() {
  const me = useMe()
  return (
    <GlassPanel as="section" aria-labelledby="governor-title" className="space-y-3 p-6">
      <h1 id="governor-title" className="flex items-center gap-2 text-lg font-bold text-white">
        <i className="fa-solid fa-landmark text-purple-400" aria-hidden="true" />
        Governor portal
      </h1>
      <p className="text-sm text-gray-300">
        Welcome, {me.user.display_name}. You are signed in as the Governor of {me.college_name}.
      </p>
      <p className="text-sm text-gray-400">
        Analytics, audits and the freeze and sign-off of results will appear here. Nothing else is
        available to this account yet.
      </p>
    </GlassPanel>
  )
}
