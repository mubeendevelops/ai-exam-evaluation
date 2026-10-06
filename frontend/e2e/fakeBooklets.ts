import type { Page, Route } from '@playwright/test'

/**
 * The booklet pipeline as the browser tests see it, stubbed next to `FakeApi` (which answers
 * sign-in). One booklet is uploaded and then moves through the machine stages by the clock, as
 * the worker would; once scored it can be opened, its lines corrected and its answer re-scored.
 * Follows docs/api/openapi.json. All names and text are invented.
 */
const STAGE_MS = 1200
const RESCORE_MS = 1500
const BOOKLET = '55555555-5555-4555-8555-555555555555'
const BLUEPRINT = '66666666-6666-4666-8666-666666666666'
const STUDENT = {
  id: '77777777-7777-4777-8777-777777777777',
  name: 'Test Student One',
  usn: 'TST001',
  class_section: 'A',
}
const COLLEGE = '11111111-1111-4111-8111-111111111111'

// A 1 x 1 PNG: the page images are only stretched boxes in these tests.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

const STAGES = ['uploaded', 'processing', 'reading', 'text_ready', 'segmented', 'scored'] as const
type Stage = (typeof STAGES)[number] | 'in_review'

interface Line {
  id: string
  box: number[]
  text: string
  score: number
  flagged: boolean
}

export class FakeBooklets {
  uploaded = false
  private since = 0
  private version = 4
  private locked = false
  private pendingUntil = 0
  private suggestion = { id: 'sg-1', mark: 1 }
  private rescored = false
  readonly lines: Line[] = [
    {
      id: 'r1',
      box: [100, 120, 900, 180],
      text: 'Lenz law opposes the change',
      score: 0.9,
      flagged: false,
    },
    { id: 'r2', box: [100, 200, 900, 260], text: 'the emf is induced', score: 0.4, flagged: true },
    {
      id: 'r3',
      box: [100, 520, 900, 580],
      text: 'Resonance is when XL equals XC',
      score: 0.88,
      flagged: false,
    },
  ]
  edits: unknown[] = []
  uploadRequests = 0

  async install(page: Page) {
    // Routes registered later run first: this one answers its own paths and hands the rest
    // (sign-in, health, questions) to the FakeApi installed before it.
    await page.route('**/api/v1/**', (route) => this.handle(route))
  }

  private stage(): Stage {
    if (this.locked) return 'in_review'
    const k = Math.min(STAGES.length - 1, Math.floor((Date.now() - this.since) / STAGE_MS))
    return STAGES[k] as Stage
  }

  private pending() {
    return Date.now() < this.pendingUntil
  }

  private bookletOut() {
    const status = this.stage()
    const done = status === 'scored' || status === 'in_review' || status === 'segmented'
    const cleaned =
      status === 'uploaded' ? 0 : done || status === 'reading' || status === 'text_ready' ? 2 : 1
    return {
      id: BOOKLET,
      status,
      student: { id: STUDENT.id, name: STUDENT.name, usn: STUDENT.usn },
      blueprint: { id: BLUEPRINT, version: 1, title: 'Mid-term Physics' },
      uploaded_by: '22222222-2222-4222-8222-222222222222',
      uploaded_at: '2026-10-06T08:00:00Z',
      version: this.version,
      page_count: status === 'uploaded' ? 0 : 2,
      pages_cleaned: cleaned,
      flagged_pages: [],
      pages_read: done || status === 'text_ready' ? 2 : 0,
      needs_text_pages: [],
      failure_reason: null,
      duplicate_of: [],
      result: null,
    }
  }

  private pageOut(number: number) {
    const read = ['text_ready', 'segmented', 'scored', 'in_review'].includes(this.stage())
    return {
      number,
      cleaned: this.stage() !== 'uploaded',
      width: 1000,
      height: 1400,
      retake_reasons: [],
      use_anyway: false,
      sharpness: 200,
      glare_share: 0,
      rotation_degrees: 0,
      rotation_guessed: false,
      skew_degrees: 0,
      cropped: true,
      perspective_corrected: false,
      neighbour_removed: false,
      page_found: true,
      image_url: `/api/v1/booklets/${BOOKLET}/pages/${number}/image`,
      original_url: null,
      text_read: read,
      needs_text: false,
      ocr_failures: [],
      text_url: read ? `/api/v1/booklets/${BOOKLET}/pages/${number}/text` : null,
    }
  }

  private regionOut(l: Line) {
    return {
      id: l.id,
      kind: 'text_line',
      box: l.box,
      text: l.text,
      chosen: 0,
      content_class: 'cursive',
      line_score: l.score,
      flagged: l.flagged,
      struck_out: false,
      read_by: ['trocr'],
      parent_id: null,
      row: null,
      col: null,
      readings: [],
    }
  }

  private answer(label: string, id: string, mark: number, pending: boolean) {
    return {
      id,
      slot_label: label,
      status: 'suggested',
      version: this.version,
      rescore_pending: pending,
      attempted: true,
      max_marks: 3,
      suggestion: {
        id: `${id}-${mark}`,
        mark,
        mark_step: 0.5,
        flags: [],
        reasons: [],
        relevance: 0.8,
        created_at: '2026-10-06T08:05:00Z',
        criteria: [],
      },
      approval: null,
      draft: null,
    }
  }

  private review() {
    const pending = this.pending()
    const mark = this.rescored && !pending ? 2.5 : this.suggestion.mark
    return {
      booklet_id: BOOKLET,
      status: this.stage() === 'in_review' ? 'in_review' : this.stage(),
      version: this.version,
      approved: false,
      amendment_in_progress: false,
      lock: this.locked
        ? {
            holder_id: '22222222-2222-4222-8222-222222222222',
            holder_name: 'Dr. Admin',
            acquired_at: '2026-10-06T08:10:00Z',
            expires_at: '2026-10-06T08:25:00Z',
            mine: true,
          }
        : null,
      can_approve: false,
      waiting: ['1', '2'],
      answers: [this.answer('1', 'a1', mark, pending), this.answer('2', 'a2', 1.5, false)],
      totals: { total: 0, max_marks: 6, slots: [] },
      sheets: [],
      rescoring: [],
      notices: [],
    }
  }

  private async handle(route: Route) {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v1', '')
    const method = request.method()
    const json = (status: number, data: unknown) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    const body =
      request.postData() && !path.endsWith('/booklets')
        ? JSON.parse(request.postData() as string)
        : undefined

    if (method === 'GET' && path === '/students') return json(200, [STUDENT])
    if (method === 'GET' && path === '/blueprints') {
      return json(200, [
        {
          id: BLUEPRINT,
          version: 1,
          title: 'Mid-term Physics',
          course_code: 'PHY-101',
          subject_id: '33333333-3333-4333-8333-333333333333',
          subject_name: 'Physics',
          total_marks: 6,
          duration_minutes: 60,
          section_count: 1,
          question_count: 2,
          unlinked_count: 0,
          owning_college_id: COLLEGE,
          owned: true,
          copied_from: null,
        },
      ])
    }
    if (method === 'GET' && path === `/blueprints/${BLUEPRINT}`) {
      return json(200, {
        id: BLUEPRINT,
        version: 1,
        title: 'Mid-term Physics',
        course_code: 'PHY-101',
        subject_id: '33333333-3333-4333-8333-333333333333',
        subject_name: 'Physics',
        total_marks: 6,
        duration_minutes: 60,
        section_count: 1,
        question_count: 2,
        unlinked_count: 0,
        owning_college_id: COLLEGE,
        owned: true,
        copied_from: null,
        document: { sections: [{ items: [{ label: '1' }, { label: '2' }] }] },
      })
    }

    if (method === 'POST' && path === '/booklets') {
      this.uploadRequests++
      this.uploaded = true
      this.since = Date.now()
      return json(201, this.bookletOut())
    }
    if (method === 'GET' && path === '/booklets') {
      const items = this.uploaded ? [this.bookletOut()] : []
      return json(200, {
        items,
        total: items.length,
        limit: 100,
        offset: 0,
        waiting: items.filter((b) => ['uploaded', 'processing', 'reading'].includes(b.status))
          .length,
        max_waiting: 5,
      })
    }
    if (!path.startsWith(`/booklets/${BOOKLET}`)) return route.fallback()

    const rest = path.slice(`/booklets/${BOOKLET}`.length)
    if (method === 'GET' && rest === '') {
      return json(200, { ...this.bookletOut(), pages: [this.pageOut(1), this.pageOut(2)] })
    }
    if (method === 'GET' && /^\/pages\/\d\/image$/.test(rest)) {
      return route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
    }
    if (method === 'GET' && rest === '/pages/1/text') {
      return json(200, {
        number: 1,
        text_read: true,
        needs_text: false,
        ocr_failures: [],
        regions: this.lines.map((l) => this.regionOut(l)),
      })
    }
    if (method === 'GET' && rest === '/pages/2/text') {
      return json(200, {
        number: 2,
        text_read: true,
        needs_text: false,
        ocr_failures: [],
        regions: [],
      })
    }
    if (method === 'POST' && rest === '/lock') {
      if (!['scored', 'in_review'].includes(this.stage()))
        return json(409, { detail: 'Not ready.' })
      if (!this.locked) this.version++
      this.locked = true
      return json(200, this.review())
    }
    if (method === 'DELETE' && rest === '/lock') {
      return route.fulfill({ status: 204 })
    }
    if (method === 'GET' && rest === '/review') return json(200, this.review())
    if (method === 'GET' && rest === '/segments') {
      const seg = (id: string, position: number, label: string, regions: string[]) => ({
        id,
        slot_label: label,
        proposed_label: null,
        position,
        source: 'rule',
        flags: [],
        match_score: null,
        region_ids: regions,
        page_ids: [],
      })
      return json(200, {
        booklet_version: this.version,
        segments: [seg('s1', 0, '1', ['r1', 'r2']), seg('s2', 1, '2', ['r3'])],
        rescoring: [],
        emptied: [],
      })
    }
    if (method === 'GET' && rest === '/diagrams') return json(200, [])
    if (method === 'POST' && rest === '/regions/r2') {
      if (body.expected_version !== this.version)
        return json(409, { detail: 'The booklet changed.' })
      this.edits.push(body)
      const line = this.lines[1] as Line
      if (typeof body.text === 'string') {
        line.text = body.text
        line.flagged = false
        line.score = 1
      }
      this.version++
      this.rescored = true
      this.pendingUntil = Date.now() + RESCORE_MS
      return json(200, {
        booklet_version: this.version,
        region: this.regionOut(line),
        rescoring: ['a1'],
      })
    }
    return json(599, { detail: `fake booklets has no ${method} ${path}` })
  }
}
