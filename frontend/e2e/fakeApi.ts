import type { Page, Route } from '@playwright/test'

interface Tenant {
  institutionId: string
  collegeName: string
  adminName: string
  email: string
  password: string
  status: 'PENDING_VERIFICATION' | 'PENDING_APPROVAL' | 'ACTIVE'
  verified: boolean
  approved: boolean
}

type Role = 'admin' | 'sme' | 'governor'

interface Signed {
  tenant: Tenant
  role: Role
}

/** The server's capability table (tarn_core.domain.personas, D177). */
const CAPABILITIES: Record<Role, string[]> = {
  admin: [
    'assign_units',
    'manage_smes',
    'manage_students',
    'manage_subjects',
    'redistribute_units',
    'return_units',
    'upload_booklets',
    'view_overview',
    'view_progress',
  ],
  sme: [
    'author_content',
    'evaluate',
    'request_reassignment',
    'submit_units',
    'view_own_work',
    'view_progress',
  ],
  governor: ['freeze_units'],
}

/**
 * Just enough of the API for the browser tests, answering in the browser (`page.route`). It
 * follows the real contract (docs/api/openapi.json): availability, registration, e-mail
 * verification, sign-in with a refresh cookie, /auth/me, logout, forgot/reset password.
 */
export class FakeApi {
  tenants = new Map<string, Tenant>()
  /** Stands in for the HttpOnly refresh cookie. */
  private signed: Signed | null = null
  forgotRequests: { institution_id: string; email: string }[] = []
  resetTokens = new Map<string, string>() // token -> institution id
  verifyTokens = new Map<string, string>()
  refreshCalls = 0
  /** The admin's lists (P25), kept in the browser test like the server would. */
  subjects: { id: string; code: string; name: string; version: number; retired: boolean }[] = [
    {
      id: '33333333-3333-4333-8333-333333333333',
      code: 'PHY-501',
      name: 'Physics',
      version: 1,
      retired: false,
    },
  ]
  smes: {
    user: Record<string, unknown>
    status: 'PENDING' | 'ACTIVE' | 'DISABLED'
    subject_ids: string[]
  }[] = []
  students: { id: string; name: string; usn: string; class_section: string }[] = []
  /** Schemas and papers (P33, P34): the newest version of each, as the server returns them. */
  blueprints = new Map<string, Blueprint>([['schema-1', blueprintOf('schema-1', MATRIX, 1)]])
  /** The versions whose paper PDF was asked for ("paper-3 v2"). */
  pdfRequests: string[] = []
  /** The question bank (P35: a new question is added here). */
  questions: Question[] = QUESTIONS.map((q) => ({ ...q }))
  private nextId = 1
  /** Who signs in next: an SME by default (the screens under test are the SME's). A test may
   * change it between sign-ins (the admin, then the SME, P26). */
  role: Role

  constructor(options: { role?: Role } = {}) {
    this.role = options.role ?? 'sme'
    this.addTenant({
      institutionId: 'SYNTH_COLLEGE',
      collegeName: 'Synthetic College',
      adminName: 'Dr. Admin',
      email: 'admin@synthetic.test',
      password: 'correct horse battery staple',
      status: 'ACTIVE',
      verified: true,
      approved: true,
    })
  }

  addTenant(tenant: Tenant) {
    this.tenants.set(tenant.institutionId, tenant)
  }

  /** The operator's `tarn tenants approve`, stubbed. */
  approve(institutionId: string) {
    const tenant = this.tenants.get(institutionId)
    if (!tenant) throw new Error(`no tenant ${institutionId}`)
    tenant.approved = true
    tenant.status = tenant.verified ? 'ACTIVE' : 'PENDING_VERIFICATION'
  }

  async install(page: Page) {
    await page.route('**/api/v1/**', (route) => this.handle(route))
  }

  private newId(prefix: string) {
    return `${prefix}-${this.nextId++}`
  }

  private user(signed: Signed) {
    return {
      id: '22222222-2222-4222-8222-222222222222',
      college_id: '11111111-1111-4111-8111-111111111111',
      display_name: signed.tenant.adminName,
      email: signed.tenant.email,
      role: signed.role,
      active: true,
    }
  }

  private async handle(route: Route) {
    const request = route.request()
    const url = new URL(request.url())
    const key = `${request.method()} ${url.pathname.replace('/api/v1', '')}`
    const body = request.postData() ? JSON.parse(request.postData() as string) : undefined
    const json = (status: number, data: unknown) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    const empty = (status: number) => route.fulfill({ status })

    switch (key) {
      case 'GET /health':
        return json(200, {
          status: 'ok',
          version: '0.0.0',
          environment: 'test',
          device: { kind: 'cpu', name: 'cpu', detail: 'CPU selected' },
          worker: { status: 'up', detail: 'Worker running' },
        })

      case 'GET /auth/password-bloom': {
        // An empty TBF1 filter: m = 64 bits, k = 1.
        const bytes = Buffer.alloc(9 + 8)
        bytes.write('TBF1', 0, 'ascii')
        bytes.writeUInt32BE(64, 4)
        bytes[8] = 1
        return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: bytes })
      }

      case 'GET /registrations/availability': {
        const id = url.searchParams.get('institution_id') ?? ''
        const valid = /^[A-Z0-9_\-*&]{1,20}$/.test(id)
        return json(200, {
          institution_id: id,
          valid,
          available: valid && !this.tenants.has(id),
          problem: valid ? null : 'Invalid Institution ID.',
        })
      }

      case 'POST /registrations': {
        if (this.tenants.has(body.institution_id)) {
          return json(409, { detail: 'That Institution ID is taken.' })
        }
        this.addTenant({
          institutionId: body.institution_id,
          collegeName: body.institution_name ?? body.institution_id,
          adminName: body.admin_name,
          email: body.email,
          password: body.password,
          status: 'PENDING_VERIFICATION',
          verified: false,
          approved: false,
        })
        this.verifyTokens.set(`college.verify-${body.institution_id}`, body.institution_id)
        return json(202, {
          institution_id: body.institution_id,
          status: 'PENDING_VERIFICATION',
          approval_required: true,
        })
      }

      case 'POST /registrations/verify-email': {
        const id = this.verifyTokens.get(body.token)
        const tenant = id ? this.tenants.get(id) : undefined
        if (!tenant) return json(400, { detail: 'Invalid or used link.' })
        this.verifyTokens.delete(body.token)
        tenant.verified = true
        tenant.status = tenant.approved ? 'ACTIVE' : 'PENDING_APPROVAL'
        return json(200, { institution_id: tenant.institutionId, status: tenant.status })
      }

      case 'POST /auth/login': {
        const tenant = this.tenants.get(String(body.institution_id).trim().toUpperCase())
        if (
          !tenant ||
          tenant.status !== 'ACTIVE' ||
          tenant.email !== body.email ||
          tenant.password !== body.password
        ) {
          return json(401, {
            detail: 'Sign-in failed. Check the Institution ID, email and password.',
          })
        }
        this.signed = { tenant, role: this.role }
        return json(200, {
          status: 'signed_in',
          access_token: 'access-token',
          token_type: 'bearer',
          expires_in: 900,
          user: this.user(this.signed),
        })
      }

      case 'POST /auth/refresh': {
        this.refreshCalls++
        if (!this.signed) return json(401, { detail: 'Sign in again.' })
        return json(200, {
          status: 'signed_in',
          access_token: 'access-token',
          token_type: 'bearer',
          expires_in: 900,
          user: this.user(this.signed),
        })
      }

      case 'GET /auth/me':
        if (!this.signed) return json(401, { detail: 'Not signed in.' })
        return json(200, {
          user: this.user(this.signed),
          institution_id: this.signed.tenant.institutionId,
          college_name: this.signed.tenant.collegeName,
          recovery_codes_left: 0,
          capabilities: [...CAPABILITIES[this.signed.role]],
        })

      case 'POST /auth/logout':
        this.signed = null
        return empty(204)

      case 'POST /auth/password/forgot': {
        this.forgotRequests.push(body)
        const tenant = this.tenants.get(String(body.institution_id).toUpperCase())
        if (tenant && tenant.email === body.email)
          this.resetTokens.set('college.reset-token', tenant.institutionId)
        return json(202, {}) // the same answer whether or not the account exists
      }

      case 'POST /auth/password/reset': {
        const id = this.resetTokens.get(body.token)
        const tenant = id ? this.tenants.get(id) : undefined
        if (!tenant) return json(400, { detail: 'Invalid or used link.' })
        this.resetTokens.delete(body.token)
        tenant.password = body.new_password
        return empty(204)
      }

      case 'GET /subjects':
        return json(200, this.subjects)

      // No units here: FakeBooklets, when installed, answers these itself (P26, P27).
      case 'GET /units':
      case 'GET /delegation-requests':
        return json(200, [])

      // The SME's bell and search (P37): nothing to tell, nothing found.
      case 'GET /notifications/unread-count':
        return json(200, { unread: 0 })
      case 'GET /notifications':
        return json(200, { items: [], total: 0, unread: 0, limit: 20, offset: 0 })
      case 'POST /notifications/read-all':
        return json(200, { marked: 0 })
      case 'GET /search':
        return json(200, { units: [], students: [], papers: [], schemas: [], questions: [] })

      // The SME's own figures (P31): nothing handed in yet.
      case 'GET /me/assignments/summary':
        return json(200, {
          academic_year: { start: '2026-07-01', end: '2027-07-01', label: '2026-27' },
          units_lifetime: 0,
          units_current_year: 0,
          completed: 0,
          active: 0,
          completion_rate: null,
          breakdown: { frozen: 0, submitted: 0, open: 0 },
          average_turnaround_days: null,
          turnaround_units: 0,
        })

      // The admin's figures (P29): nothing has happened in the browser tests' college.
      case 'GET /overview':
        return json(200, {
          smes: { active: 0, idle: 0, suspended: 0 },
          units: { open: 0, submitted: 0, frozen: 0, overdue: 0 },
          booklets: { processing: 0, ready: 0, in_review: 0, approved: 0, attention: 0, total: 0 },
          activity: [],
        })
      case 'GET /overview/progress':
        return json(200, { smes: [], units: [] })
      case 'GET /overview/system':
        return json(200, { queue_queued: 0, queue_running: 0, storage_bytes: 0 })

      case 'POST /subjects': {
        if (this.subjects.some((s) => s.code === body.code))
          return json(409, { detail: 'That subject code is taken.' })
        const subject = { id: this.newId('sub'), ...body, version: 1, retired: false }
        this.subjects.push(subject)
        return json(201, subject)
      }

      case 'POST /accounts': {
        if (this.smes.some((a) => a.user.email === body.email))
          return json(409, { detail: 'That email already has an account.' })
        const account = {
          user: {
            id: this.newId('sme'),
            college_id: '11111111-1111-4111-8111-111111111111',
            display_name: body.display_name,
            email: body.email,
            role: 'sme',
            active: true,
          },
          status: 'PENDING' as const,
          locked_until: null,
          force_reset: false,
          subject_ids: body.subject_ids ?? [],
          idle: true,
        }
        this.smes.push(account)
        return json(201, account)
      }

      case 'POST /students': {
        if (this.students.some((s) => s.usn === String(body.usn).toUpperCase()))
          return json(409, { detail: 'A student with this USN is already on the roster.' })
        const student = { id: this.newId('stu'), ...body, usn: String(body.usn).toUpperCase() }
        this.students.push(student)
        return json(201, student)
      }

      case 'POST /blueprints/validate': {
        // The real rules live in the backend (tests in Python); here only the title decides.
        const issues = String(body.title ?? '').trim()
          ? []
          : [{ path: 'title', message: 'must not be empty' }]
        return json(200, {
          valid: issues.length === 0,
          issues,
          warnings: [],
          sections: [],
          computed_total: body.total_marks,
          question_count: 0,
          unlinked: [],
        })
      }

      case 'GET /schemas': {
        const schemas = [...this.blueprints.values()].filter((b) => b.kind === 'schema')
        return json(200, {
          items: schemas.map((b) => ({
            ...b,
            used_in: [...this.blueprints.values()].filter((p) => p.from_schema === b.id).length,
          })),
          batches: [],
        })
      }

      case 'GET /blueprints': {
        const kind = url.searchParams.get('kind')
        return json(
          200,
          [...this.blueprints.values()].filter((b) => !kind || b.kind === kind),
        )
      }

      case 'GET /papers': {
        const papers = [...this.blueprints.values()].filter((b) => b.kind === 'paper')
        return json(200, {
          items: papers.map((b) => ({
            ...b,
            created_at: b.updated_at,
            in_use: false,
          })),
          total: papers.length,
          limit: 12,
          offset: 0,
          descriptors: [...new Set(papers.map((b) => b.descriptor).filter(Boolean))],
        })
      }

      case 'POST /papers': {
        const id = this.newId('paper')
        const document = {
          ...STARTER,
          title: body.title,
          subject_id: body.subject_id,
          descriptor: body.descriptor ?? '',
        }
        const made = blueprintOf(id, document, 1)
        this.blueprints.set(id, made)
        return json(201, made)
      }

      case 'GET /question-topics':
        return json(200, ['Electromagnetism', 'Optics'])

      case 'GET /questions': {
        const level = url.searchParams.get('difficulty')
        const word = (url.searchParams.get('keyword') ?? '').toLowerCase()
        const items = this.questions.filter(
          (q) =>
            (!level || q.difficulty === level) &&
            (!word || `${q.text} ${q.code}`.toLowerCase().includes(word)),
        )
        return json(200, { items, total: items.length, limit: 12, offset: 0 })
      }

      case 'POST /questions': {
        const id = `q-${this.questions.length + 1}`
        const made = {
          id,
          version: 1,
          code: String(body.code),
          text: String(body.text),
          max_marks: Number(body.max_marks),
          difficulty: String(body.difficulty),
          category: String(body.category ?? ''),
          subject_id: String(body.subject_id),
          subject_name: 'Physics',
          key_count: body.reference_answer ? 1 : 0,
        }
        this.questions.push(made)
        return json(201, questionDetail(made))
      }

      case 'GET /accounts':
        return json(200, [
          {
            user: this.user({ tenant: this.tenants.get('SYNTH_COLLEGE')!, role: 'admin' }),
            status: 'ACTIVE',
            locked_until: null,
            force_reset: false,
            subject_ids: [],
          },
          ...this.smes.map((a) => ({ locked_until: null, force_reset: false, ...a })),
        ])
      case 'GET /students': {
        const q = (url.searchParams.get('q') ?? '').toLowerCase()
        return json(
          200,
          this.students.filter(
            (s) => !q || s.usn.toLowerCase().startsWith(q) || s.name.toLowerCase().includes(q),
          ),
        )
      }

      default: {
        const subject = /^(PUT|POST) \/subjects\/([^/]+?)(\/retire)?$/.exec(key)
        if (subject) {
          const found = this.subjects.find((s) => s.id === subject[2])
          if (!found) return json(404, { detail: 'Not found.' })
          if (subject[3]) found.retired = true
          else Object.assign(found, body, { version: found.version + 1 })
          return json(200, found)
        }
        const account =
          /^(POST|DELETE) \/accounts\/([^/]+)\/(disable|enable|subjects)(?:\/([^/]+))?$/.exec(key)
        if (account) {
          const found = this.smes.find((a) => a.user.id === account[2])
          if (!found) return json(404, { detail: 'Not found.' })
          if (account[3] === 'disable') found.status = 'DISABLED'
          else if (account[3] === 'enable') found.status = 'ACTIVE'
          else if (account[1] === 'POST') found.subject_ids.push(account[4] as string)
          else found.subject_ids = found.subject_ids.filter((id) => id !== account[4])
          return json(200, found.subject_ids)
        }
        const student = /^PUT \/students\/([^/]+)$/.exec(key)
        if (student) {
          const found = this.students.find((s) => s.id === student[1])
          if (!found) return json(404, { detail: 'Not found.' })
          Object.assign(found, body, { usn: String(body.usn).toUpperCase() })
          return json(200, found)
        }
        const blueprint = /^(GET|PUT) \/blueprints\/([^/]+)$/.exec(key)
        if (blueprint) {
          const found = this.blueprints.get(blueprint[2] as string)
          if (!found) return json(404, { detail: 'Not found.' })
          if (blueprint[1] === 'GET') return json(200, found)
          const saved = blueprintOf(found.id, { ...body, kind: found.kind }, found.version + 1)
          this.blueprints.set(found.id, saved)
          return json(200, saved)
        }
        const generate = /^POST \/blueprints\/([^/]+)\/paper$/.exec(key)
        if (generate) {
          const schema = this.blueprints.get(generate[1] as string)
          if (!schema) return json(404, { detail: 'Not found.' })
          const id = this.newId('paper')
          const paper = blueprintOf(
            id,
            {
              ...(schema.document as Doc),
              kind: 'paper',
              title: `${schema.title} paper`,
              from_schema: schema.id,
            },
            1,
          )
          this.blueprints.set(id, paper)
          return json(201, paper)
        }
        const pdf = /^GET \/blueprints\/([^/]+)\/versions\/(\d+)\/paper\.pdf$/.exec(key)
        if (pdf) {
          const found = this.blueprints.get(pdf[1] as string)
          if (!found || found.kind !== 'paper') return json(404, { detail: 'Not found.' })
          this.pdfRequests.push(`${found.id} v${pdf[2]}`)
          return route.fulfill({
            status: 200,
            contentType: 'application/pdf',
            body: tinyPdf(found.title),
          })
        }
        const usage = /^GET \/questions\/(q-\d+)\/usage$/.exec(key)
        if (usage) {
          const papers = [...this.blueprints.values()]
            .filter(
              (b) =>
                b.kind === 'paper' &&
                JSON.stringify(b.document).includes(`"question_id":"${usage[1]}"`),
            )
            .map((b) => ({ id: b.id, title: b.title, exam_code: b.exam_code, open_units: [] }))
          return json(200, { papers })
        }
        const one = /^(GET|PUT) \/questions\/(q-\d+)$/.exec(key)
        const found = one ? this.questions.find((q) => q.id === one[2]) : undefined
        if (found && one?.[1] === 'PUT') {
          Object.assign(found, {
            version: found.version + 1,
            text: String(body.text),
            code: String(body.code),
            max_marks: Number(body.max_marks),
            difficulty: String(body.difficulty),
            category: String(body.category ?? ''),
          })
          return json(200, questionDetail(found))
        }
        if (found) return json(200, questionDetail(found))
        return json(599, { detail: `fake API has no ${key}` })
      }
    }
  }
}

const SUBJECT = '33333333-3333-4333-8333-333333333333'

interface Question {
  id: string
  version: number
  code: string
  text: string
  max_marks: number
  difficulty: string
  category: string
  subject_id: string
  subject_name: string
  key_count: number
}

const QUESTIONS: Question[] = [
  ['q-1', 'PHY-Q1', 'State Lenz’s law.', 'easy', 4],
  ['q-2', 'PHY-Q2', 'Define resonance in an LCR circuit.', 'hard', 5],
].map(([id, code, text, difficulty, marks]) => ({
  id: String(id),
  version: 1,
  code: String(code),
  text: String(text),
  max_marks: Number(marks),
  difficulty: String(difficulty),
  category: 'Electromagnetism',
  subject_id: SUBJECT,
  subject_name: 'Physics',
  key_count: 1,
}))

function questionDetail(q: Question) {
  return {
    ...q,
    reference_answers: [
      {
        id: 'a-1',
        version: 1,
        text: 'The induced emf opposes the change.',
        guidance_only: false,
        synthetic: false,
      },
    ],
    rubric: {
      criteria: [
        {
          version: 1,
          criterion: {
            id: 'c-1',
            type: 'semantic',
            label: 'States the law',
            weight: q.max_marks,
            params: { reference_statement: 'The emf opposes the change that causes it.' },
          },
        },
      ],
      total: q.max_marks,
      max_marks: q.max_marks,
      complete: true,
    },
    glossary: { teacher_terms: ['induced emf'], reference_labels: [], terms: ['induced emf'] },
    key_files: [],
    diagrams: [],
  }
}

// --- schemas and papers (P33, P34) ------------------------------------------------------------

type Doc = Record<string, unknown> & {
  title: string
  kind: 'schema' | 'paper'
  sections: { items: { question_id?: string | null }[] }[]
}

export type Blueprint = ReturnType<typeof blueprintOf>

function section(marks: number[]) {
  return {
    label: 'A',
    title: '',
    method: 'semantic_rubric',
    choice: { rule: 'all' },
    items: marks.map((m, i) => ({
      type: 'question',
      label: String(i + 1),
      marks: m,
      question_id: null,
    })),
  }
}

const HEADER = {
  schema_version: '1.2',
  course_code: 'PHY-501',
  exam_code: '',
  subject_id: SUBJECT,
  duration_minutes: 180,
  negative_marking: 0,
  mark_step: 0.5,
  target_batch: '',
  weightage_note: '',
  description: '',
  descriptor: '',
  from_schema: null,
}

/** A schema whose two slots fit the two bank questions (4 and 5 marks). */
const MATRIX: Doc = {
  ...HEADER,
  kind: 'schema',
  title: 'Physics Matrix',
  total_marks: 9,
  sections: [section([4, 5])],
}

/** The server's starter paper (P34): five unlinked 2-mark questions. */
const STARTER: Doc = {
  ...HEADER,
  kind: 'paper',
  title: '',
  total_marks: 10,
  sections: [section([2, 2, 2, 2, 2])],
}

function blueprintOf(id: string, document: Doc, version: number) {
  const leaves = document.sections.flatMap((s) => s.items)
  return {
    id,
    version,
    title: document.title,
    course_code: String(document.course_code ?? ''),
    exam_code: String(document.exam_code ?? ''),
    subject_id: SUBJECT,
    subject_name: 'Physics',
    total_marks: Number(document.total_marks),
    duration_minutes: (document.duration_minutes as number | null) ?? null,
    section_count: document.sections.length,
    question_count: leaves.length,
    unlinked_count: leaves.filter((l) => !l.question_id).length,
    kind: document.kind,
    target_batch: String(document.target_batch ?? ''),
    weightage_note: String(document.weightage_note ?? ''),
    description: String(document.description ?? ''),
    descriptor: String(document.descriptor ?? ''),
    from_schema: (document.from_schema as string | null) ?? null,
    updated_at: '2026-11-09T10:00:00Z',
    structure_locked: false,
    document: { ...document, schema_version: '1.2' },
  }
}

/** A real one-page PDF naming the paper, small enough to write by hand (offsets computed). */
function tinyPdf(title: string): Buffer {
  const text = title.replace(/[()\\]/g, ' ').replace(/[^\x20-\x7e]/g, '?')
  const stream = `BT /F1 18 Tf 72 760 Td (${text}) Tj ET`
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ]
  let out = '%PDF-1.4\n'
  const offsets: number[] = []
  objects.forEach((o, i) => {
    offsets.push(out.length)
    out += `${i + 1} 0 obj\n${o}\nendobj\n`
  })
  const xref = out.length
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  for (const o of offsets) out += `${String(o).padStart(10, '0')} 00000 n \n`
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(out, 'latin1')
}
