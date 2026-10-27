import type { components } from '../api/schema'

type Me = components['schemas']['MeOut']
export type Capability = Me['capabilities'][number]
export type Role = Me['user']['role']

/** What a persona may do comes from the server (`/auth/me`, D177): the screens never decide
 * from the role name alone. */
export function can(me: Me, capability: Capability): boolean {
  return me.capabilities.includes(capability)
}

/** Where a signed-in person lands: the admin on the admin page, an SME on the Q&A tab, the
 * Governor on its placeholder page (D159). */
export function homeOf(me: Me): string {
  if (can(me, 'manage_smes')) return '/admin'
  if (can(me, 'author_content')) return '/qna'
  return '/governor'
}

/** How a role is written in the UI ("SME", never "teacher"; D176). */
export const roleLabel: Record<Role, string> = {
  admin: 'Admin',
  sme: 'SME',
  governor: 'Governor',
}
