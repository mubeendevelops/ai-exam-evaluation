import type { components } from '../api/schema'
import { COUNTS, maths, physics, unitOf } from './adminFixtures'
import { healthy, meOf, tokenOf } from './fixtures'
import { mockApi, type Call, type Reply } from './mockApi'

type Summary = components['schemas']['AssignmentSummaryOut']

export function summaryOf(over: Partial<Summary> = {}): Summary {
  return {
    academic_year: { start: '2026-07-01', end: '2027-07-01', label: '2026-27' },
    units_lifetime: 4,
    units_current_year: 3,
    completed: 3,
    active: 1,
    completion_rate: 75,
    breakdown: { frozen: 1, submitted: 2, open: 1 },
    average_turnaround_days: 2.5,
    turnaround_units: 3,
    ...over,
  }
}

/** Three units of the SME's: open and overdue (Physics, 1 of 4), open (Mathematics, 3 of 4),
 * submitted (Physics, 2 of 2). */
export const myUnits = [
  unitOf({
    id: 'unit-1',
    code: 'U-0001',
    due_date: '2020-01-15',
    paper: { ...unitOf().paper, title: 'Physics mid-term' },
    counts: { ...COUNTS, total: 4, completed: 1 },
  }),
  unitOf({
    id: 'unit-2',
    code: 'U-0002',
    due_date: '2099-03-01',
    subject: { id: maths.id, code: maths.code, name: maths.name },
    paper: { ...unitOf().paper, id: 'paper-2', title: 'Algebra final', exam_code: 'MAT-FIN' },
    counts: { ...COUNTS, total: 4, completed: 3 },
  }),
  unitOf({
    id: 'unit-3',
    code: 'U-0003',
    due_date: '2099-01-10',
    state: 'submitted',
    paper: { ...unitOf().paper, id: 'paper-3', title: 'Optics quiz', exam_code: 'PHY-OPT' },
    counts: { ...COUNTS, total: 2, completed: 2 },
  }),
]

/** An SME who is signed in, with what the SME frame and My Assignments read. */
export function smeSession(
  extra: Record<string, Reply | ((call: Call) => Reply | Promise<Reply>)> = {},
) {
  return mockApi({
    'POST /api/v1/auth/refresh': { json: tokenOf({ role: 'sme' }) },
    'GET /api/v1/auth/me': { json: meOf({ role: 'sme' }) },
    'GET /api/v1/health': { json: healthy },
    'GET /api/v1/subjects': { json: [physics, maths] },
    'GET /api/v1/me/assignments/summary': { json: summaryOf() },
    'GET /api/v1/units': { json: myUnits },
    'GET /api/v1/delegation-requests': { json: [] },
    ...extra,
  })
}
