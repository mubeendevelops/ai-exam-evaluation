/**
 * The strength hint under every new-password field. Mirrors the server policy (NIST style:
 * a minimum length, no composition rules, common/breached list); the server decides.
 */
export const MIN_PASSWORD_LENGTH = 12
export const STRONG_PASSWORD_LENGTH = 16

export type Strength =
  | { level: 'idle' }
  | { level: 'weak'; reason: 'short' | 'common' }
  | { level: 'acceptable' }
  | { level: 'strong' }

export function strengthOf(password: string, common: boolean): Strength {
  if (password === '') return { level: 'idle' }
  if (common) return { level: 'weak', reason: 'common' }
  if (password.length < MIN_PASSWORD_LENGTH) return { level: 'weak', reason: 'short' }
  if (password.length < STRONG_PASSWORD_LENGTH) return { level: 'acceptable' }
  return { level: 'strong' }
}
