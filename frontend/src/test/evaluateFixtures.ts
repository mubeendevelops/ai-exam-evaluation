import type { components } from '../api/schema'

export type Booklet = components['schemas']['BookletOut']
export type BookletDetail = components['schemas']['BookletDetailOut']
export type Page = components['schemas']['PageOut']
export type PageText = components['schemas']['PageTextOut']
export type Region = components['schemas']['RegionOut']
export type Segment = components['schemas']['SegmentOut']
export type Review = components['schemas']['ReviewOut']
export type ReviewAnswer = components['schemas']['ReviewAnswerOut']
export type StudentDiagram = components['schemas']['StudentDiagramOut']
export type Graph = components['schemas']['DiagramGraphOut']

export const BOOKLET = 'b-1'
export const BLUEPRINT = 'bp-1'
// All names are invented: no student data in the repository.
export const STUDENT = { id: 'st-1', name: 'Test Student One', usn: 'TST001', class_section: 'A' }

export function booklet(over: Partial<Booklet> = {}): Booklet {
  return {
    id: BOOKLET,
    status: 'scored',
    student: { id: STUDENT.id, name: STUDENT.name, usn: STUDENT.usn },
    blueprint: { id: BLUEPRINT, version: 1, title: 'Mid-term Physics' },
    uploaded_by: 'u-1',
    uploaded_at: '2026-10-06T08:00:00Z',
    version: 4,
    page_count: 2,
    pages_cleaned: 2,
    flagged_pages: [],
    pages_read: 2,
    needs_text_pages: [],
    failure_reason: null,
    duplicate_of: [],
    result: null,
    ...over,
  }
}

export function page(number: number, over: Partial<Page> = {}): Page {
  return {
    number,
    cleaned: true,
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
    text_read: true,
    needs_text: false,
    ocr_failures: [],
    text_url: `/api/v1/booklets/${BOOKLET}/pages/${number}/text`,
    ...over,
  }
}

export function detail(over: Partial<BookletDetail> = {}): BookletDetail {
  return { ...booklet(), pages: [page(1), page(2)], ...over }
}

export function region(
  id: string,
  box: number[],
  text: string,
  over: Partial<Region> = {},
): Region {
  return {
    id,
    kind: 'text_line',
    box,
    text,
    chosen: 0,
    content_class: 'cursive',
    line_score: 0.9,
    flagged: false,
    struck_out: false,
    read_by: ['trocr'],
    parent_id: null,
    row: null,
    col: null,
    readings: [],
    ...over,
  }
}

export const PAGE_TEXT: Record<number, PageText> = {
  1: {
    number: 1,
    text_read: true,
    needs_text: false,
    ocr_failures: [],
    regions: [
      region('r1', [100, 100, 900, 160], 'Lenz law opposes the change'),
      region('r2', [100, 180, 900, 240], 'the emf is induced', { line_score: 0.4, flagged: true }),
      region('r3', [100, 400, 900, 460], 'Resonance is when XL equals XC'),
    ],
  },
  2: {
    number: 2,
    text_read: true,
    needs_text: false,
    ocr_failures: [],
    regions: [region('r4', [100, 100, 900, 160], 'stray words')],
  },
}

export function segment(
  id: string,
  position: number,
  regionIds: string[],
  label: string | null,
  over: Partial<Segment> = {},
): Segment {
  return {
    id,
    slot_label: label,
    proposed_label: null,
    position,
    source: 'rule',
    flags: [],
    match_score: null,
    region_ids: regionIds,
    page_ids: [],
    ...over,
  }
}

export const SEGMENTS = {
  booklet_version: 5,
  segments: [
    segment('s1', 0, ['r1', 'r2'], '1'),
    segment('s2', 1, ['r3'], '2'),
    segment('s3', 2, ['r4'], null),
  ],
}

export function answer(
  id: string,
  label: string,
  mark: number | null,
  over: Partial<ReviewAnswer> = {},
): ReviewAnswer {
  return {
    id,
    slot_label: label,
    status: 'suggested',
    version: 2,
    rescore_pending: false,
    attempted: true,
    max_marks: 3,
    suggestion: {
      id: `sg-${id}-${mark}`,
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
    ...over,
  }
}

export function review(over: Partial<Review> = {}): Review {
  return {
    booklet_id: BOOKLET,
    status: 'in_review',
    version: 5,
    approved: false,
    amendment_in_progress: false,
    lock: {
      holder_id: 'u-1',
      holder_name: 'Asha Rao',
      acquired_at: '2026-10-06T08:10:00Z',
      expires_at: '2026-10-06T08:25:00Z',
      mine: true,
    },
    can_approve: false,
    waiting: ['1', '2'],
    answers: [answer('a1', '1', 2), answer('a2', '2', 1)],
    totals: { total: 0, max_marks: 6, slots: [] },
    sheets: [],
    rescoring: [],
    notices: [],
    ...over,
  }
}

export function graph(over: Partial<Graph> = {}): Graph {
  return {
    schema_version: '1.0',
    nodes: [
      {
        id: 'n1',
        shape: 'terminal',
        label: 'Start',
        box: [100, 100, 300, 160],
        confidence: 0.9,
        label_confidence: 0.8,
      },
      {
        id: 'n2',
        shape: 'process',
        label: 'Add',
        box: [100, 260, 300, 320],
        confidence: 0.9,
        label_confidence: 0.8,
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'n1',
        target: 'n2',
        label: '',
        directed: true,
        confidence: 0.9,
        box: null,
        tail: null,
        head: null,
      },
    ],
    free_labels: [],
    recognizer: { name: 'shape-detector-v1', version: '1' },
    label_engines: [],
    edited_by_teacher: false,
    ...over,
  }
}
