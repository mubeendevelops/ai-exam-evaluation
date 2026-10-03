/**
 * The exam blueprint of the Schema Designer: the public document (docs/api/blueprint.schema.json,
 * version 1.0), the editable form model it is generated from, and the arithmetic the live
 * panel shows before the server has answered. The server (`POST /api/v1/blueprints/validate`)
 * has the last word on validity; nothing here decides that.
 */

export const SCHEMA_VERSION = '1.0'

export type Method =
  'exact_pattern_match' | 'omr_bubble_scan' | 'keyword_formula' | 'semantic_rubric' | 'diagram'

export const METHODS: { value: Method; label: string }[] = [
  { value: 'exact_pattern_match', label: 'Exact Pattern Match' },
  { value: 'omr_bubble_scan', label: 'OMR Bubble Scan' },
  { value: 'keyword_formula', label: 'Keyword + Formula' },
  { value: 'semantic_rubric', label: 'Semantic Rubric' },
  { value: 'diagram', label: 'Diagram' },
]

export const NEGATIVE_MARKING = ['0', '0.25', '0.5']
export const MARK_STEPS = ['0.25', '0.5', '1']

// ---- the document -------------------------------------------------------------------------

/** A number as typed: a real number when it parses, otherwise the text, so the server can say
 * what is wrong with it instead of the form hiding it. */
export type Num = number | string

export interface DocStep {
  label: string
  marks: Num
}
export interface DocPart {
  label: string
  marks: Num
  question_id: string | null
  steps?: DocStep[]
}
export interface DocQuestion {
  label: string
  marks: Num
  question_id: string | null
  parts?: DocPart[]
  steps?: DocStep[]
}
export type DocItem =
  ({ type: 'question' } & DocQuestion) | { type: 'or'; alternatives: DocQuestion[] }
export interface DocSection {
  label: string
  title: string
  method: Method
  choice: { rule: 'all' } | { rule: 'any'; n: Num }
  items: DocItem[]
}
export interface BlueprintDocument {
  schema_version: typeof SCHEMA_VERSION
  title: string
  course_code: string
  subject_id: string
  duration_minutes: Num | null
  total_marks: Num
  negative_marking: Num
  mark_step: Num
  sections: DocSection[]
}

// ---- the form -----------------------------------------------------------------------------

export interface FormStep {
  key: string
  label: string
  marks: string
}
export interface FormPart {
  key: string
  label: string
  marks: string
  questionId: string
  steps: FormStep[]
}
export interface FormQuestion {
  key: string
  label: string
  marks: string
  questionId: string
  parts: FormPart[]
  steps: FormStep[]
}
export type FormItem =
  | { key: string; kind: 'question'; question: FormQuestion }
  | { key: string; kind: 'or'; alternatives: FormQuestion[] }
export interface FormSection {
  key: string
  label: string
  title: string
  method: Method
  rule: 'all' | 'any'
  /** N of "answer any N of M"; text because it is being typed. */
  n: string
  items: FormItem[]
}
export interface FormState {
  title: string
  courseCode: string
  subjectId: string
  duration: string
  /** Typed total; null follows the calculated total. */
  totalMarks: string | null
  negativeMarking: string
  markStep: string
  sections: FormSection[]
}

// A random prefix per page load: keys of a draft restored from session storage never collide
// with keys made after the reload.
const prefix = Math.random().toString(36).slice(2, 8)
let counter = 0
export function uid(): string {
  counter += 1
  return `${prefix}-${counter}`
}

export function newQuestion(label: string, marks: string): FormQuestion {
  return { key: uid(), label, marks, questionId: '', parts: [], steps: [] }
}

export function newSection(
  label: string,
  firstQuestion: number,
  count = 5,
  marks = '2',
): FormSection {
  return {
    key: uid(),
    label,
    title: '',
    method: 'semantic_rubric',
    rule: 'all',
    n: String(count),
    items: uniformItems(firstQuestion, count, marks),
  }
}

export function uniformItems(first: number, count: number, marks: string): FormItem[] {
  return Array.from({ length: count }, (_, i) => ({
    key: uid(),
    kind: 'question' as const,
    question: newQuestion(String(first + i), marks),
  }))
}

export function initialForm(): FormState {
  return {
    title: '',
    courseCode: '',
    subjectId: '',
    duration: '',
    totalMarks: null,
    negativeMarking: '0',
    markStep: '0.5',
    sections: [newSection('A', 1)],
  }
}

// ---- form -> document ---------------------------------------------------------------------

export function numberOf(text: string): Num {
  const trimmed = text.trim()
  const value = Number(trimmed)
  return trimmed !== '' && Number.isFinite(value) ? value : text
}

function stepsOf(steps: FormStep[]): { steps?: DocStep[] } {
  return steps.length === 0
    ? {}
    : { steps: steps.map((s) => ({ label: s.label, marks: numberOf(s.marks) })) }
}

function questionOf(q: FormQuestion): DocQuestion {
  const doc: DocQuestion = {
    label: q.label,
    marks: numberOf(q.marks),
    question_id: q.questionId.trim() === '' ? null : q.questionId.trim(),
  }
  if (q.parts.length > 0) {
    doc.parts = q.parts.map((p) => ({
      label: p.label,
      marks: numberOf(p.marks),
      question_id: p.questionId.trim() === '' ? null : p.questionId.trim(),
      ...stepsOf(p.steps),
    }))
  }
  return { ...doc, ...stepsOf(q.steps) }
}

export function toDocument(form: FormState): BlueprintDocument {
  return {
    schema_version: SCHEMA_VERSION,
    title: form.title,
    course_code: form.courseCode,
    subject_id: form.subjectId,
    duration_minutes: form.duration.trim() === '' ? null : numberOf(form.duration),
    total_marks: numberOf(form.totalMarks ?? String(calculatedTotal(form))),
    negative_marking: numberOf(form.negativeMarking),
    mark_step: numberOf(form.markStep),
    sections: form.sections.map((s) => ({
      label: s.label,
      title: s.title,
      method: s.method,
      choice: s.rule === 'all' ? { rule: 'all' } : { rule: 'any', n: numberOf(s.n) },
      items: s.items.map((item): DocItem =>
        item.kind === 'or'
          ? { type: 'or', alternatives: item.alternatives.map(questionOf) }
          : { type: 'question', ...questionOf(item.question) },
      ),
    })),
  }
}

// ---- document -> form ---------------------------------------------------------------------

function formSteps(steps: DocStep[] | undefined): FormStep[] {
  return (steps ?? []).map((s) => ({ key: uid(), label: s.label, marks: String(s.marks) }))
}

function formQuestion(q: DocQuestion): FormQuestion {
  return {
    key: uid(),
    label: q.label,
    marks: String(q.marks),
    questionId: q.question_id ?? '',
    parts: (q.parts ?? []).map((p) => ({
      key: uid(),
      label: p.label,
      marks: String(p.marks),
      questionId: p.question_id ?? '',
      steps: formSteps(p.steps),
    })),
    steps: formSteps(q.steps),
  }
}

/** Loads a document (an example, or a saved blueprint) into the form. */
export function fromDocument(doc: BlueprintDocument): FormState {
  return {
    title: doc.title,
    courseCode: doc.course_code,
    subjectId: doc.subject_id,
    duration: doc.duration_minutes === null ? '' : String(doc.duration_minutes),
    totalMarks: String(doc.total_marks),
    negativeMarking: String(doc.negative_marking),
    markStep: String(doc.mark_step),
    sections: doc.sections.map((s) => ({
      key: uid(),
      label: s.label,
      title: s.title,
      method: s.method,
      rule: s.choice.rule,
      n: s.choice.rule === 'any' ? String(s.choice.n) : String(s.items.length),
      items: s.items.map((item): FormItem =>
        item.type === 'or'
          ? { key: uid(), kind: 'or', alternatives: item.alternatives.map(formQuestion) }
          : { key: uid(), kind: 'question', question: formQuestion(item) },
      ),
    })),
  }
}

// ---- arithmetic for the live banner -------------------------------------------------------

function round2(value: number): number {
  return Math.round(value * 100) / 100
}

function marksOf(text: string): number {
  const value = Number(text)
  return text.trim() !== '' && Number.isFinite(value) && value > 0 ? value : 0
}

export function itemMarks(item: FormItem): number {
  return marksOf(item.kind === 'or' ? (item.alternatives[0]?.marks ?? '') : item.question.marks)
}

/** N of "any N of M" as the form has it; all items for the "all" rule. */
export function countedItems(section: FormSection): number {
  if (section.rule === 'all') return section.items.length
  const n = Math.trunc(Number(section.n))
  return Number.isFinite(n) ? Math.min(Math.max(n, 0), section.items.length) : 0
}

/** The most a student can earn in the section: the N largest items. */
export function sectionMax(section: FormSection): number {
  const sorted = section.items.map(itemMarks).sort((a, b) => b - a)
  return round2(sorted.slice(0, countedItems(section)).reduce((a, b) => a + b, 0))
}

export function calculatedTotal(form: FormState): number {
  return round2(form.sections.reduce((sum, s) => sum + sectionMax(s), 0))
}

export function slotsOf(section: FormSection): FormQuestion[] {
  return section.items.flatMap((i) => (i.kind === 'or' ? i.alternatives : [i.question]))
}

/** Numbered questions on the paper (both alternatives of an OR pair count). */
export function questionCount(form: FormState): number {
  return form.sections.reduce((sum, s) => sum + slotsOf(s).length, 0)
}

// ---- editing helpers ----------------------------------------------------------------------

/** One more than the largest whole-number label on the paper, ignoring ``skip``'s questions. */
export function nextNumber(form: FormState, skip?: string): number {
  let top = 0
  for (const section of form.sections) {
    if (section.key === skip) continue
    for (const q of slotsOf(section)) {
      const n = Number(q.label)
      if (Number.isInteger(n) && n > top) top = n
    }
  }
  return top + 1
}

/** "N questions of M marks": replaces the section's items with plain numbered questions. */
export function fillSection(form: FormState, key: string, count: number, marks: string): FormState {
  const start = nextNumber(form, key)
  return {
    ...form,
    sections: form.sections.map((s) =>
      s.key === key
        ? {
            ...s,
            items: uniformItems(start, count, marks),
            n: s.rule === 'any' ? String(Math.min(Number(s.n) || count, count)) : String(count),
          }
        : s,
    ),
  }
}

/** Numbers every question 1..n in paper order; sub-part labels become a, b, c. */
export function renumber(form: FormState): FormState {
  let n = 0
  const number = (q: FormQuestion): FormQuestion => {
    n += 1
    return {
      ...q,
      label: String(n),
      parts: q.parts.map((p, i) => ({ ...p, label: String.fromCharCode(97 + (i % 26)) })),
    }
  }
  return {
    ...form,
    sections: form.sections.map((s) => ({
      ...s,
      items: s.items.map((item) =>
        item.kind === 'or'
          ? { ...item, alternatives: item.alternatives.map(number) }
          : { ...item, question: number(item.question) },
      ),
    })),
  }
}

export function nextSectionLabel(form: FormState): string {
  const used = new Set(form.sections.map((s) => s.label))
  for (let i = 0; i < 26; i++) {
    const label = String.fromCharCode(65 + i)
    if (!used.has(label)) return label
  }
  return String(form.sections.length + 1)
}

export function fileNameOf(form: FormState): string {
  const base = (form.courseCode || form.title).trim().toLowerCase()
  const slug = base.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return `${slug || 'exam'}.blueprint.json`
}
