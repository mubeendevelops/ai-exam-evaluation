import { usePasswordStrength } from '../../hooks/usePasswordBloom'
import { MIN_PASSWORD_LENGTH, STRONG_PASSWORD_LENGTH, type Strength } from '../../lib/password'

const BASE = 'rounded px-2 py-0.5 font-mono text-[10px] font-bold'

function describe(strength: Strength): {
  label: string
  chip: string
  bar: string
  width: number
  help: string
} {
  switch (strength.level) {
    case 'idle':
      return {
        label: 'Idle',
        chip: 'bg-gray-800 text-gray-400',
        bar: 'bg-purple-500',
        width: 0,
        help: `Checks against a probabilistic hash array of common passwords. At least ${MIN_PASSWORD_LENGTH} characters; no other composition rules.`,
      }
    case 'weak':
      return strength.reason === 'common'
        ? {
            label: 'FLAGGED / WEAK',
            chip: 'border border-red-500/30 bg-red-950 text-red-400',
            bar: 'bg-red-500',
            width: 25,
            help: 'The Bloom filter says this password is probably on the list of common or breached passwords. Choose another; the server makes the final check.',
          }
        : {
            label: 'TOO SHORT',
            chip: 'border border-red-500/30 bg-red-950 text-red-400',
            bar: 'bg-red-500',
            width: 25,
            help: `Use at least ${MIN_PASSWORD_LENGTH} characters. A long passphrase beats a short complicated password.`,
          }
    case 'acceptable':
      return {
        label: 'ACCEPTABLE',
        chip: 'border border-yellow-500/30 bg-yellow-950 text-yellow-400',
        bar: 'bg-yellow-500',
        width: 65,
        help: `Passed the common-password hint. Longer is stronger: ${STRONG_PASSWORD_LENGTH} or more characters is best.`,
      }
    case 'strong':
      return {
        label: 'HIGH SECURE',
        chip: 'border border-emerald-500/30 bg-emerald-950 text-emerald-400',
        bar: 'bg-emerald-500',
        width: 100,
        help: 'Passed the common-password hint. The server makes the final check.',
      }
  }
}

/** "Bloom Filter Security Verification" box of the registration form, shown under any new password. */
export function PasswordHint({ password }: { password: string }) {
  const view = describe(usePasswordStrength(password))
  return (
    <div className="mt-2 space-y-2 rounded-xl border border-gray-800 bg-gray-950/60 p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-gray-400">Bloom Filter Security Verification:</span>
        <span data-testid="strength-status" className={`${BASE} ${view.chip}`}>
          {view.label}
        </span>
      </div>
      <div
        role="meter"
        aria-label="Password strength"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={view.width}
        className="h-1.5 w-full overflow-hidden rounded-full bg-gray-800"
      >
        <div
          className={`h-1.5 transition-all duration-300 ${view.bar}`}
          style={{ width: `${view.width}%` }}
        />
      </div>
      <p className="text-[10px] leading-tight text-gray-400">{view.help}</p>
    </div>
  )
}
