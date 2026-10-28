import type { components } from '../api/schema'
import { healthy, meOf, tokenOf, userOf } from './fixtures'
import { mockApi, type Call, type Reply } from './mockApi'

type Account = components['schemas']['AccountOut']
type Subject = components['schemas']['SubjectOut']
type Student = components['schemas']['StudentOut']
type WorkUnit = components['schemas']['UnitOut']
type WorkUnitDetail = components['schemas']['UnitDetailOut']
type Booklet = components['schemas']['BookletOut']

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

export const COUNTS = {
  total: 0,
  processing: 0,
  needs_retake: 0,
  failed: 0,
  ready: 0,
  in_review: 0,
  completed: 0,
}

export function unitOf(over: Partial<WorkUnit> = {}): WorkUnit {
  return {
    id: 'unit-1',
    code: 'U-0001',
    paper: {
      id: 'paper-1',
      version: 2,
      title: 'Physics mid-term',
      exam_code: 'PHY-2026-MID',
      course_code: 'PHY101',
      total_marks: 50,
    },
    subject: { id: physics.id, code: physics.code, name: physics.name },
    year: '2024',
    owner: { id: asha.id, name: asha.display_name },
    due_date: '2026-12-01',
    state: 'open',
    created_at: '2026-10-28T08:00:00Z',
    version: 1,
    counts: COUNTS,
    ...over,
  }
}

/** A booklet row of a unit, statuses only (the admin's view, no marks). */
export function unitBooklet(over: Partial<Booklet> = {}): Booklet {
  return {
    id: 'bk-1',
    status: 'uploaded',
    student: { id: students[0]!.id, name: students[0]!.name, usn: students[0]!.usn },
    unit: { id: 'unit-1', code: 'U-0001' },
    blueprint: { id: 'paper-1', version: 2, title: 'Physics mid-term' },
    uploaded_by: adminUser.id,
    uploaded_at: '2026-10-28T09:00:00Z',
    version: 1,
    page_count: 0,
    pages_cleaned: 0,
    flagged_pages: [],
    pages_read: 0,
    needs_text_pages: [],
    failure_reason: null,
    duplicate_of: [],
    result: null,
    ...over,
  }
}

export function unitDetailOf(
  booklets: Booklet[] = [],
  over: Partial<WorkUnitDetail> = {},
): WorkUnitDetail {
  return {
    ...unitOf({ counts: { ...COUNTS, total: booklets.length } }),
    booklets,
    waiting: 0,
    max_waiting: 5,
    max_files: 20,
    ...over,
  }
}

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
    'GET /api/v1/units': { json: [] },
    ...extra,
  })
}
