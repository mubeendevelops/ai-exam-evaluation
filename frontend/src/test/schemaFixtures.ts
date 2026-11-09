import type { components } from '../api/schema'
import type { BlueprintDocument } from '../lib/blueprint'
import type { Call, Reply } from './mockApi'
import { physics } from './adminFixtures'

type Blueprint = components['schemas']['BlueprintOut']
type Card = components['schemas']['SchemaCardOut']
type Check = components['schemas']['ValidationOut']

/** A 1.2 document of one section of five 2-mark questions. */
export function documentOf(over: Partial<BlueprintDocument> = {}): BlueprintDocument {
  return {
    schema_version: '1.2',
    kind: 'schema',
    title: 'Midterm Matrix',
    course_code: 'PHY',
    exam_code: '',
    subject_id: physics.id,
    duration_minutes: null,
    total_marks: 10,
    negative_marking: 0,
    mark_step: 0.5,
    target_batch: 'Class 12',
    weightage_note: '40% conceptual, 60% analytical',
    description: '',
    descriptor: '',
    from_schema: null,
    sections: [
      {
        label: 'A',
        title: '',
        method: 'semantic_rubric',
        choice: { rule: 'all' },
        items: [1, 2, 3, 4, 5].map((n) => ({
          type: 'question' as const,
          label: String(n),
          marks: 2,
          question_id: null,
        })),
      },
    ],
    ...over,
  }
}

/** The API's blueprint around a document. */
export function blueprintOf(id: string, document: BlueprintDocument, version = 1): Blueprint {
  return {
    id,
    version,
    title: document.title,
    course_code: document.course_code,
    exam_code: document.exam_code ?? '',
    subject_id: document.subject_id,
    subject_name: physics.name,
    total_marks: Number(document.total_marks),
    duration_minutes: null,
    section_count: document.sections.length,
    question_count: 5,
    unlinked_count: 5,
    kind: document.kind ?? 'paper',
    target_batch: document.target_batch ?? '',
    weightage_note: document.weightage_note ?? '',
    description: document.description ?? '',
    descriptor: document.descriptor ?? '',
    from_schema: document.from_schema ?? null,
    updated_at: '2026-10-20T10:00:00Z',
    document: document as unknown as Record<string, unknown>,
  }
}

export function cardOf(over: Partial<Card> = {}): Card {
  return {
    id: 'schema-1',
    version: 2,
    title: 'Midterm Matrix',
    course_code: 'PHY',
    subject_id: physics.id,
    subject_name: physics.name,
    target_batch: 'Class 12',
    weightage_note: '40% conceptual, 60% analytical',
    description: '',
    total_marks: 10,
    section_count: 1,
    updated_at: '2026-10-20T10:00:00Z',
    used_in: 3,
    ...over,
  }
}

/** The server's verdict stand-in: a document with a title is valid (rules are tested in
 * Python). */
export function verdictOf(call: Call): Reply {
  const body = call.body as BlueprintDocument
  const issues = body.title.trim() === '' ? [{ path: 'title', message: 'must not be empty' }] : []
  const check: Check = {
    valid: issues.length === 0,
    issues,
    warnings: [],
    sections: [],
    computed_total: Number(body.total_marks),
    question_count: 5,
    unlinked: [],
  }
  return { json: check }
}
