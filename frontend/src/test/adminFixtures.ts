import type { components } from '../api/schema'
import { healthy, meOf, tokenOf, userOf } from './fixtures'
import { mockApi, type Call, type Reply } from './mockApi'

type Account = components['schemas']['AccountOut']
type Subject = components['schemas']['SubjectOut']
type Student = components['schemas']['StudentOut']

export const physics: Subject = {
  id: 'sub-1',
  code: 'PHY',
  name: 'Physics',
  version: 1,
  retired: false,
}
export const maths: Subject = {
  id: 'sub-2',
  code: 'MAT',
  name: 'Mathematics',
  version: 1,
  retired: false,
}
export const chemistry: Subject = {
  id: 'sub-3',
  code: 'CHE',
  name: 'Chemistry',
  version: 2,
  retired: true,
}
export const subjects: Subject[] = [physics, maths, chemistry]

export const adminUser = userOf({
  id: 'a-1',
  display_name: 'Dr. Admin',
  email: 'admin@college.test',
  role: 'admin',
})
export const asha = userOf({
  id: 't-1',
  display_name: 'Asha Rao',
  email: 'asha@college.test',
  role: 'sme',
})
export const ravi = userOf({
  id: 't-2',
  display_name: 'Ravi Kumar',
  email: 'ravi@college.test',
  role: 'sme',
  active: false,
})

export const ashaAccount: Account = {
  user: asha,
  status: 'ACTIVE',
  locked_until: '2999-01-01T00:00:00Z',
  force_reset: true,
  subject_ids: [physics.id, chemistry.id],
}
export const raviAccount: Account = {
  user: ravi,
  status: 'DISABLED',
  locked_until: null,
  force_reset: false,
  subject_ids: [],
}
export const accounts: Account[] = [
  { user: adminUser, status: 'ACTIVE', locked_until: null, force_reset: false, subject_ids: [] },
  ashaAccount,
  raviAccount,
]

export const students: Student[] = [
  { id: 's-1', name: 'Asha Student', usn: '1AB21CS001', class_section: 'CSE-A' },
  { id: 's-2', name: 'Ravi Student', usn: '1AB21CS002', class_section: '' },
]

/** An admin who is signed in, with the lists the admin screens read. */
export function adminSession(
  extra: Record<string, Reply | ((call: Call) => Reply | Promise<Reply>)> = {},
) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf({ role: 'admin' }) },
    'GET /api/v1/auth/me': { json: meOf({ role: 'admin' }) },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/accounts': { json: accounts },
    'GET /api/v1/subjects': { json: subjects },
    'GET /api/v1/students': { json: students },
    ...extra,
  })
}
