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

type Report = components['schemas']['MyReportOut']

/** My Reports on a plausible set: the quarters of 2026 (the current one last). */
export function reportOf(over: Partial<Report> = {}): Report {
  return {
    academic_year: { start: '2026-07-01', end: '2027-07-01', label: '2026-27' },
    turnaround_lifetime: { average_days: 2.5, units: 6 },
    turnaround_year: { average_days: 1.8, units: 4 },
    on_time_rate: 66.7,
    units_handed_in: 6,
    quarters: [
      {
        label: '2026 Q1',
        start: '2026-01-01',
        end: '2026-04-01',
        units_submitted: 0,
        booklets_approved: 0,
      },
      {
        label: '2026 Q2',
        start: '2026-04-01',
        end: '2026-07-01',
        units_submitted: 1,
        booklets_approved: 12,
      },
      {
        label: '2026 Q3',
        start: '2026-07-01',
        end: '2026-10-01',
        units_submitted: 2,
        booklets_approved: 40,
      },
      {
        label: '2026 Q4',
        start: '2026-10-01',
        end: '2027-01-01',
        units_submitted: 3,
        booklets_approved: 25,
      },
    ],
    open_units: 2,
    booklets_pending: 17,
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

type Notice = components['schemas']['NotificationOut']

export const noResults: components['schemas']['SearchOut'] = {
  units: [],
  students: [],
  papers: [],
  schemas: [],
  questions: [],
}

export function noticeOf(over: Partial<Notice> = {}): Notice {
  return {
    id: 'n-1',
    kind: 'assigned',
    unit_id: 'unit-1',
    unit_code: 'U-0001',
    due_date: '2026-11-20',
    request_id: null,
    note: null,
    created_at: '2026-11-10T08:00:00Z',
    read_at: null,
    ...over,
  }
}

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
    'GET /api/v1/me/reports': { json: reportOf() },
    'GET /api/v1/units': { json: myUnits },
    'GET /api/v1/delegation-requests': { json: [] },
    'GET /api/v1/notifications/unread-count': { json: { unread: 0 } },
    'GET /api/v1/notifications': {
      json: { items: [], total: 0, unread: 0, limit: 20, offset: 0 },
    },
    'GET /api/v1/search': { json: noResults },
    ...extra,
  })
}
