/**
 * The rubric editor's form model and its conversion to and from the API's criteria
 * (`CriterionBody`). The server decides what is valid; this only turns what is typed into the
 * request, and the stored criteria back into something editable.
 */
import type { components } from '../api/schema'

export type CriterionType = 'list' | 'numeric' | 'semantic' | 'diagram'
export type Component = 'whole' | 'nodes' | 'edges' | 'labels'

type ApiCriterion =
  | components['schemas']['ListCriterion']
  | components['schemas']['NumericCriterion']
  | components['schemas']['SemanticCriterion']
  | components['schemas']['DiagramCriterion']

export const CRITERION_TYPES: { value: CriterionType; label: string; hint: string }[] = [
  { value: 'list', label: 'List / keywords', hint: 'The student names items from a list.' },
  { value: 'numeric', label: 'Numeric value', hint: 'One step with an expected number.' },
  { value: 'semantic', label: 'Semantic statement', hint: 'The answer says this, in any words.' },
  { value: 'diagram', label: 'Diagram', hint: 'Compared with a reference diagram.' },
]

export const COMPONENTS: { value: Component; label: string }[] = [
  { value: 'whole', label: 'Whole diagram' },
  { value: 'nodes', label: 'Nodes' },
  { value: 'edges', label: 'Edges' },
  { value: 'labels', label: 'Labels' },
]

export interface CriterionForm {
  key: string
  /** Set for a criterion that exists already: saving makes a new version of it. */
  id: string | null
  type: CriterionType
  label: string
  weight: string
  /** List: one item per line, `term | synonym, synonym`. */
  items: string
  required: string
  expected: string
  tolerance: string
  unit: string
  statement: string
  diagramId: string
  component: Component
}

let counter = 0
const prefix = Math.random().toString(36).slice(2, 7)
function key(): string {
  counter += 1
  return `c${prefix}-${counter}`
}

export function newCriterion(type: CriterionType, weight = ''): CriterionForm {
  return {
    key: key(),
    id: null,
    type,
    label: '',
    weight,
    items: '',
    required: '1',
    expected: '',
    tolerance: '0',
    unit: '',
    statement: '',
    diagramId: '',
    component: 'whole',
  }
}

/** Items typed as lines `term | synonym, synonym` -> the API's items. */
export function parseItems(text: string): { term: string; synonyms: string[] }[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
    .map((line) => {
      const [term = '', rest = ''] = line.split('|', 2).map((part) => part.trim())
      return {
        term,
        synonyms: rest
          .split(',')
          .map((s) => s.trim())
          .filter((s) => s !== ''),
      }
    })
}

export function itemsText(items: { term: string; synonyms?: string[] }[]): string {
  return items
    .map((i) => (i.synonyms?.length ? `${i.term} | ${i.synonyms.join(', ')}` : i.term))
    .join('\n')
}

/** What was typed; text that is not a number is sent as is so the server can reject it. */
function num(text: string): number {
  const value = Number(text)
  return text.trim() !== '' && Number.isFinite(value) ? value : (text as unknown as number)
}

export function toBody(c: CriterionForm): ApiCriterion {
  const common = { id: c.id, label: c.label.trim(), weight: num(c.weight) }
  switch (c.type) {
    case 'list':
      return {
        ...common,
        type: 'list',
        params: { items: parseItems(c.items), required_count: num(c.required) },
      }
    case 'numeric':
      return {
        ...common,
        type: 'numeric',
        params: { expected: num(c.expected), tolerance: num(c.tolerance || '0'), unit: c.unit },
      }
    case 'semantic':
      return { ...common, type: 'semantic', params: { reference_statement: c.statement } }
    case 'diagram':
      return {
        ...common,
        type: 'diagram',
        params: { reference_diagram_id: c.diagramId, component: c.component },
      }
  }
}

export function fromBody(c: ApiCriterion): CriterionForm {
  const form: CriterionForm = {
    ...newCriterion(c.type, String(c.weight)),
    id: c.id ?? null,
    label: c.label,
  }
  switch (c.type) {
    case 'list':
      return {
        ...form,
        items: itemsText(c.params.items),
        required: String(c.params.required_count),
      }
    case 'numeric':
      return {
        ...form,
        expected: String(c.params.expected),
        tolerance: String(c.params.tolerance ?? 0),
        unit: c.params.unit ?? '',
      }
    case 'semantic':
      return { ...form, statement: c.params.reference_statement }
    case 'diagram':
      return {
        ...form,
        diagramId: c.params.reference_diagram_id,
        component: c.params.component ?? 'whole',
      }
  }
}

export function weightTotal(criteria: CriterionForm[]): number {
  const total = criteria.reduce((sum, c) => sum + (Number(c.weight) || 0), 0)
  return Math.round(total * 100) / 100
}

/** Splits a total over `count` criteria for a fresh rubric (the remainder goes to the last). */
export function evenWeights(total: number, count: number): string[] {
  if (count <= 0) return []
  const share = Math.floor((total / count) * 100) / 100
  const weights = Array.from({ length: count }, () => share)
  weights[count - 1] = Math.round((total - share * (count - 1)) * 100) / 100
  return weights.map(String)
}
