import type { components } from '../../api/schema'
import type { Tone } from '../ui'

export type Booklet = components['schemas']['BookletOut']
export type BookletDetail = components['schemas']['BookletDetailOut']
export type BookletStatus = Booklet['status']
export type Page = components['schemas']['PageOut']
export type RetakeReason = Page['retake_reasons'][number]

export interface StatusMeta {
  label: string
  tone: Tone
  /** Still being worked on by the machine: the list refreshes and a beam sweeps the page. */
  working: boolean
}

export const STATUS: Record<BookletStatus, StatusMeta> = {
  uploaded: { label: 'Queued', tone: 'gray', working: true },
  processing: { label: 'Cleaning pages', tone: 'cyan', working: true },
  needs_retake: { label: 'Retake needed', tone: 'amber', working: false },
  pages_ready: { label: 'Pages ready', tone: 'cyan', working: true },
  reading: { label: 'Reading handwriting', tone: 'cyan', working: true },
  text_ready: { label: 'Text read', tone: 'cyan', working: true },
  segmented: { label: 'Splitting into answers', tone: 'purple', working: true },
  scored: { label: 'Ready for review', tone: 'emerald', working: false },
  in_review: { label: 'In review', tone: 'indigo', working: false },
  approved: { label: 'Approved', tone: 'emerald', working: false },
  amendment_in_progress: { label: 'Amendment in progress', tone: 'amber', working: false },
  approved_amended: { label: 'Approved (amended)', tone: 'emerald', working: false },
  failed: { label: 'Failed', tone: 'red', working: false },
}

export const FAILURE: Record<NonNullable<Booklet['failure_reason']>, string> = {
  unreadable_file: 'The file could not be read.',
  too_many_pages: 'The booklet has too many pages.',
  processing_failed: 'Cleaning the pages failed.',
  reading_failed: 'Reading the handwriting failed.',
  segmentation_failed: 'Splitting into answers failed.',
  diagrams_failed: 'Reading the diagrams failed.',
  scoring_failed: 'Suggesting marks failed.',
}

export const RETAKE_REASON: Record<RetakeReason, string> = {
  blurry: 'The writing is blurred. Hold the phone steady and retake it.',
  glare: 'Glare covers part of the page. Tilt the page away from the light.',
  low_resolution: 'The photo is too small to read. Move closer or use a higher resolution.',
  no_page_found: 'No page could be told apart from its surroundings. Put it on a plain surface.',
}

/** From scored on, a teacher can open the booklet; before that the machine is still at work. */
export function isReviewable(status: BookletStatus): boolean {
  return ['scored', 'in_review', 'approved', 'amendment_in_progress', 'approved_amended'].includes(
    status,
  )
}

export function isApproved(status: BookletStatus): boolean {
  return ['approved', 'amendment_in_progress', 'approved_amended'].includes(status)
}

export function resultText(b: Booklet): string {
  if (b.result) return `${b.result.total} / ${b.result.max_marks}`
  if (b.status === 'failed') return '—'
  return isReviewable(b.status) ? 'Awaiting approval' : 'Pending'
}
