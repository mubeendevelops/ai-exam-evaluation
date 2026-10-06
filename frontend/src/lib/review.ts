/** What the evaluation view works out from the review: the paper's slots, the progress through
 * the answers, mark checks, feedback tags and the diagram comparison document (R6). */
import type { components } from '../api/schema'

type ReviewAnswer = components['schemas']['ReviewAnswerOut']

// --- the paper's slots -----------------------------------------------------------------------

export interface SlotInfo {
  label: string
  /** The question bank entry the slot is marked against; null while it is unlinked. */
  questionId: string | null
  marks: number
  sectionLabel: string
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

/** The answer slots of a blueprint document by label (`"7"`, or `"12.a"` for a sub-part). */
export function slotInfo(document: Record<string, unknown>): Map<string, SlotInfo> {
  const slots = new Map<string, SlotInfo>()
  const sections = Array.isArray(document.sections) ? document.sections : []
  for (const section of sections) {
    const sectionLabel = text((section as { label?: unknown }).label) ?? ''
    const add = (label: string, q: { marks?: unknown; question_id?: unknown }) => {
      slots.set(label, {
        label,
        questionId: text(q.question_id) ?? null,
        marks: typeof q.marks === 'number' ? q.marks : 0,
        sectionLabel,
      })
    }
    const addQuestion = (question: unknown) => {
      if (typeof question !== 'object' || question === null) return
      const q = question as { label?: unknown; parts?: unknown; marks?: unknown }
      const label = text(q.label)
      if (label === undefined) return
      if (Array.isArray(q.parts) && q.parts.length > 0) {
        for (const part of q.parts) {
          const partLabel = text((part as { label?: unknown }).label)
          if (partLabel !== undefined) add(`${label}.${partLabel}`, part as { marks?: unknown })
        }
      } else {
        add(label, q)
      }
    }
    const items = (section as { items?: unknown }).items
    if (!Array.isArray(items)) continue
    for (const item of items) {
      if (typeof item !== 'object' || item === null) continue
      const alternatives = (item as { alternatives?: unknown }).alternatives
      if (Array.isArray(alternatives)) alternatives.forEach(addQuestion)
      else addQuestion(item)
    }
  }
  return slots
}

// --- progress ----------------------------------------------------------------------------------

/** Where an answer stands for the teacher. */
export type Stage = 'todo' | 'skipped' | 'approved' | 'draft' | 'none'

export function stageOf(a: ReviewAnswer): Stage {
  if (!a.attempted) return 'none'
  if (a.draft) return 'draft'
  if (a.status === 'approved') return 'approved'
  if (a.status === 'skipped') return 'skipped'
  return 'todo'
}

export interface Progress {
  /** Answers with text: the ones that need a decision. */
  attempted: number
  approved: number
  skipped: number
  drafts: number
}

export function progressOf(answers: ReviewAnswer[]): Progress {
  const stages = answers.map(stageOf)
  const count = (s: Stage) => stages.filter((x) => x === s).length
  return {
    attempted: stages.filter((s) => s !== 'none').length,
    approved: count('approved'),
    skipped: count('skipped'),
    drafts: count('draft'),
  }
}

/** The next (or, with -1, previous) answer with text; the ends stay put. */
export function neighbour(answers: ReviewAnswer[], label: string | null, step: 1 | -1) {
  const list = answers.filter((a) => a.attempted)
  const at = list.findIndex((a) => a.slot_label === label)
  if (at < 0) return step === 1 ? list[0] : list[list.length - 1]
  return list[at + step]
}

/** The next answer still needing a decision (to do, skipped, or an amendment draft), looking on
 * from `label` and wrapping round, so a skipped answer is met again. Never `label` itself. */
export function nextOpen(answers: ReviewAnswer[], label: string | null): ReviewAnswer | undefined {
  const list = answers.filter((a) => a.attempted)
  const at = list.findIndex((a) => a.slot_label === label)
  const order = [...list.slice(at + 1), ...list.slice(0, Math.max(at, 0))]
  return order.find((a) => {
    const stage = stageOf(a)
    return (stage === 'todo' || stage === 'skipped' || stage === 'draft') && a.slot_label !== label
  })
}

// --- marks -------------------------------------------------------------------------------------

export function formatMark(value: number): string {
  return String(Number(value.toFixed(2)))
}

/** Why `input` is not a mark this paper accepts (0 to the question's marks, in the paper's
 * step), or null when it is one. The server checks the same rule. */
export function markProblem(input: string, step: number, max: number): string | null {
  const t = input.trim()
  if (t === '') return 'Enter a mark.'
  const n = Number(t)
  if (!Number.isFinite(n)) return 'Enter a number.'
  if (n < 0 || n > max) return `A mark is between 0 and ${formatMark(max)}.`
  const k = n / step
  if (Math.abs(k - Math.round(k)) > 1e-6) return `Marks go in steps of ${formatMark(step)}.`
  return null
}

// --- feedback tags -----------------------------------------------------------------------------

export const DEFAULT_TAGS = [
  'Correct',
  'Partially correct',
  'Key point missing',
  'Needs an example',
  'Off topic',
  'Illegible handwriting',
  'Diagram incomplete',
  'Well explained',
] as const

export const MAX_TAGS = 10
export const MAX_TAG_LENGTH = 40
const TAGS_KEY = 'tarn.feedback-tags'

export function cleanTag(raw: string): string {
  return raw.trim().replace(/\s+/g, ' ').slice(0, MAX_TAG_LENGTH)
}

/** The teacher's own tags, kept in this browser (a convenience, not shared data). */
export function loadCustomTags(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(TAGS_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return []
    return parsed.filter((t): t is string => typeof t === 'string' && cleanTag(t) !== '')
  } catch {
    return []
  }
}

export function saveCustomTags(tags: string[]): void {
  try {
    localStorage.setItem(TAGS_KEY, JSON.stringify(tags))
  } catch {
    // storage blocked: the tags last for this visit only
  }
}

// --- the diagram comparison (docs/api/diagram-comparison.schema.json) --------------------------

export type MatchKind = 'exact' | 'close' | 'none'

export interface CompareNode {
  id: string
  shape: string
  label: string
  matched_ref: string | null
  shape_match: boolean | null
}

export interface CompareRefNode {
  id: string
  shape: string
  label: string
  matched_student: string | null
}

export interface CompareEdge {
  ref: [string, string] | null
  student: [string, string] | null
  status: 'present' | 'missing' | 'reversed' | 'extra'
  student_edge: string | null
}

export interface Comparison {
  student_diagram_id: string | null
  similarity: number | null
  sub_scores: { nodes: number; edges: number; labels: number } | null
  nodes: CompareNode[]
  reference_nodes: CompareRefNode[]
  edges: CompareEdge[]
}

const array = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

/** The parts of an R6 document the overlay draws; null when it is not one. */
export function parseComparison(document: Record<string, unknown>): Comparison | null {
  if (!Array.isArray(document.nodes) || !Array.isArray(document.edges)) return null
  const sub = document.sub_scores as Comparison['sub_scores'] | undefined
  return {
    student_diagram_id: text(document.student_diagram_id) ?? null,
    similarity: typeof document.similarity === 'number' ? document.similarity : null,
    sub_scores: sub && typeof sub === 'object' ? sub : null,
    nodes: array(document.nodes) as CompareNode[],
    reference_nodes: array(document.reference_nodes) as CompareRefNode[],
    edges: array(document.edges) as CompareEdge[],
  }
}

export function refNodeName(c: Comparison, id: string): string {
  const node = c.reference_nodes.find((n) => n.id === id)
  return node ? node.label.trim() || `(${node.shape})` : id
}

export function studentNodeName(c: Comparison, id: string): string {
  const node = c.nodes.find((n) => n.id === id)
  return node ? node.label.trim() || `(${node.shape})` : id
}
