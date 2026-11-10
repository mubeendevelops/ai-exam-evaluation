import type { components } from '../api/schema'
import { fromBody, type CriterionForm } from './rubric'

type Question = components['schemas']['QuestionOut']
type Difficulty = components['schemas']['Difficulty']

/** What a new question starts from. */
export interface Seed {
  subjectId: string
  marks: string
  text?: string
  category?: string
  difficulty?: Difficulty
  /** The first reference answer's text. */
  answer?: string
  criteria?: CriterionForm[]
  /** Said to the SME: what the copy left out. */
  note?: string
}

/**
 * A copy of `source` as the start of a new question (the editor's "Clone as start"): its text,
 * topic, difficulty, first reference answer and rubric. The code is never copied (it is unique),
 * nor are files and diagrams; a diagram criterion needs a diagram of the new question, so it
 * is left out. With a slot's `marks` that differ from the source's, the rubric is left out too:
 * its weights would not add up.
 */
export function seedFrom(source: Question, marks?: string | null): Seed {
  const sameMarks = !marks || Number(marks) === source.max_marks
  const left: string[] = []
  let criteria: CriterionForm[] | undefined
  if (sameMarks) {
    const all = source.rubric.criteria.map((c) => ({ ...fromBody(c.criterion), id: null }))
    criteria = all.filter((c) => c.type !== 'diagram')
    if (criteria.length < all.length) left.push('its diagram criterion')
  } else {
    left.push('its rubric, because the slot carries other marks')
  }
  if (source.diagrams.length > 0 || source.key_files.length > 0) left.push('its files')
  return {
    subjectId: source.subject_id,
    marks: marks || String(source.max_marks),
    text: source.text,
    category: source.category,
    difficulty: source.difficulty,
    answer: source.reference_answers.find((a) => !a.guidance_only)?.text,
    ...(criteria ? { criteria } : {}),
    ...(left.length > 0
      ? { note: `Started from ${source.code}; not copied: ${left.join(', ')}.` }
      : {}),
  }
}
