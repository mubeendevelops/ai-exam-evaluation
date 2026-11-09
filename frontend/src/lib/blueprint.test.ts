import { describe, expect, it } from 'vitest'
import ciExample from '../../../docs/api/blueprint.example-ci.json'
import iprExample from '../../../docs/api/blueprint.example-ipr.json'
import objectiveExample from '../../../docs/api/blueprint.example-objective.json'
import {
  calculatedTotal,
  fileNameOf,
  fillSection,
  fromDocument,
  initialForm,
  newSection,
  nextNumber,
  numberOf,
  questionCount,
  renumber,
  sectionMax,
  slotsOf,
  starterSchema,
  toDocument,
  type BlueprintDocument,
  type FormState,
} from './blueprint'
import { loadDraft } from './draft'

const examples = {
  ci: ciExample as BlueprintDocument,
  ipr: iprExample as BlueprintDocument,
  objective: objectiveExample as BlueprintDocument,
}

describe('blueprint form <-> document', () => {
  it.each(Object.entries(examples))('round-trips the %s example exactly', (_name, doc) => {
    expect(toDocument(fromDocument(doc))).toEqual(doc)
  })

  it('computes the totals of the real papers with the choice rules', () => {
    const ci = fromDocument(examples.ci)
    expect(ci.sections.map(sectionMax)).toEqual([10, 20, 20]) // 5x2, 4x5, 2x10
    expect(calculatedTotal(ci)).toBe(50)
    expect(questionCount(ci)).toBe(17)

    const ipr = fromDocument(examples.ipr)
    expect(ipr.sections.map(sectionMax)).toEqual([15, 30, 15]) // 5x3, 3x10, Q12 or Q13 (10 + 5)
    expect(calculatedTotal(ipr)).toBe(60)
    expect(questionCount(ipr)).toBe(13) // both alternatives are numbered questions
    expect(slotsOf(ipr.sections[2]!).map((q) => q.parts.map((p) => p.marks))).toEqual([
      ['10', '5'],
      ['10', '5'],
    ])
  })

  it('counts the N largest items when marks differ', () => {
    const form = fromDocument(examples.ci)
    const section = form.sections[0]!
    section.items.forEach((item, i) => {
      if (item.kind === 'question') item.question.marks = String(i + 1) // 1..7
    })
    expect(sectionMax(section)).toBe(7 + 6 + 5 + 4 + 3)
  })

  it('follows the calculated total until a total is typed', () => {
    const form = fromDocument(examples.ci)
    expect(toDocument({ ...form, totalMarks: null }).total_marks).toBe(50)
    expect(toDocument({ ...form, totalMarks: '55' }).total_marks).toBe(55)
  })

  it('sends what it cannot read as text, so the server can explain it', () => {
    expect(numberOf('2.5')).toBe(2.5)
    expect(numberOf(' 3 ')).toBe(3)
    expect(numberOf('')).toBe('')
    expect(numberOf('two')).toBe('two')
    const form = initialForm()
    form.sections[0]!.items = [
      { key: 'x', kind: 'question', question: { ...slotsOf(form.sections[0]!)[0]!, marks: 'abc' } },
    ]
    expect(toDocument(form).sections[0]!.items[0]).toMatchObject({ marks: 'abc' })
  })

  it('leaves out empty parts and steps, and writes a blank link as null', () => {
    const doc = toDocument(initialForm())
    const first = doc.sections[0]!.items[0]!
    expect(first).toEqual({ type: 'question', label: '1', marks: 2, question_id: null })
    expect(doc.duration_minutes).toBeNull()
  })
})

describe('editing helpers', () => {
  it('fills a section with N plain questions numbered after the others', () => {
    const form: FormState = {
      ...initialForm(),
      sections: [newSection('A', 1, 5, '2'), newSection('B', 6, 3, '5')],
    }
    const filled = fillSection(form, form.sections[1]!.key, 7, '5')
    expect(slotsOf(filled.sections[1]!).map((q) => q.label)).toEqual([
      '6',
      '7',
      '8',
      '9',
      '10',
      '11',
      '12',
    ])
    expect(filled.sections[1]!.items).toHaveLength(7)
    expect(sectionMax(filled.sections[1]!)).toBe(35)
    expect(nextNumber(filled)).toBe(13)
  })

  it('keeps N within M when the section shrinks', () => {
    const form = initialForm()
    const section = { ...form.sections[0]!, rule: 'any' as const, n: '5' }
    const filled = fillSection({ ...form, sections: [section] }, section.key, 3, '2')
    expect(filled.sections[0]!.n).toBe('3')
  })

  it('renumbers questions in paper order and sub-parts a, b, c', () => {
    const form = fromDocument(examples.ipr)
    form.sections[0]!.items.pop()
    const done = renumber(form)
    const labels = done.sections.flatMap((s) => slotsOf(s).map((q) => q.label))
    expect(labels).toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'])
    expect(slotsOf(done.sections[2]!)[0]!.parts.map((p) => p.label)).toEqual(['a', 'b'])
  })

  it('names the download after the course code', () => {
    expect(fileNameOf({ ...initialForm(), courseCode: '19AU0003', title: 'x' })).toBe(
      '19au0003.blueprint.json',
    )
    expect(fileNameOf({ ...initialForm(), title: 'Mid-Term 2026!' })).toBe(
      'mid-term-2026.blueprint.json',
    )
    expect(fileNameOf(initialForm())).toBe('exam.blueprint.json')
  })
})

describe('document 1.2: schemas and papers (P33)', () => {
  it('starts a schema with the dialog fields and no links or exam code', () => {
    const doc = starterSchema({
      title: 'Matrix',
      subjectId: 's-1',
      courseCode: 'PHY',
      targetBatch: ' Class 12 ',
      weightageNote: '40/60',
    })
    expect(doc).toMatchObject({
      schema_version: '1.2',
      kind: 'schema',
      title: 'Matrix',
      course_code: 'PHY',
      exam_code: '',
      target_batch: 'Class 12',
      weightage_note: '40/60',
      from_schema: null,
      total_marks: 10,
    })
  })

  it('writes no links, exam code or descriptor for a schema, whatever the form held', () => {
    const form = fromDocument(ciExample as BlueprintDocument)
    const doc = toDocument({ ...form, kind: 'schema', descriptor: 'Quiz', fromSchema: 'x' })
    expect(doc.exam_code).toBe('')
    expect(doc.descriptor).toBe('')
    expect(doc.from_schema).toBeNull()
    expect(JSON.stringify(doc.sections)).not.toMatch(/"question_id":"/)
  })

  it('reads older documents as papers and keeps a paper origin', () => {
    const { kind, target_batch, ...older } = ciExample as BlueprintDocument
    expect([kind, target_batch]).toEqual(['paper', '2021 batch'])
    const form = fromDocument({ ...older, schema_version: '1.1' })
    expect(form).toMatchObject({ kind: 'paper', targetBatch: '' })
    const paper = toDocument({ ...form, fromSchema: 'schema-1' })
    expect(paper).toMatchObject({
      kind: 'paper',
      from_schema: 'schema-1',
      exam_code: 'CIV-2021-JUN',
    })
  })

  it('reads a version 1 draft with the new fields filled in', () => {
    const v1: Partial<FormState> = initialForm()
    delete v1.kind
    delete v1.targetBatch
    globalThis.sessionStorage.setItem(
      'tarn.schema-designer.draft',
      JSON.stringify({ version: 1, form: { ...v1, title: 'Old draft' } }),
    )
    expect(loadDraft()).toMatchObject({ title: 'Old draft', kind: 'paper', targetBatch: '' })
    globalThis.sessionStorage.setItem('tarn.schema-designer.draft', JSON.stringify({ version: 9 }))
    expect(loadDraft()).toBeNull()
  })
})
