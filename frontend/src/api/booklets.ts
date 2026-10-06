import { authFetch } from './client'
import { NETWORK_PROBLEM, problemOf } from './errors'
import type { components } from './schema'

type BookletOut = components['schemas']['BookletOut']

export type BookletUpload =
  | { ok: true; booklet: BookletOut }
  | { ok: false; kind: 'duplicate'; message: string; duplicateOf: string[] }
  | { ok: false; kind: 'error'; message: string }

const REFUSALS: Record<number, string> = {
  413: 'The upload is too large. Send fewer or smaller pages.',
  415: 'Send one PDF, or JPEG and PNG page images.',
  429: 'You already have the most booklets the queue allows. Wait for one to finish.',
}

/**
 * Uploads one booklet: one PDF, or its page images in order (multipart: the form is built here
 * because the generated client does not send files). A file the college has already sent is
 * refused with 409 and the booklets it matches; the caller repeats with `allowDuplicate`.
 */
export async function uploadBooklet(
  studentId: string,
  blueprintId: string,
  files: File[],
  allowDuplicate = false,
): Promise<BookletUpload> {
  const form = new FormData()
  form.set('student_id', studentId)
  form.set('blueprint_id', blueprintId)
  if (allowDuplicate) form.set('allow_duplicate', 'true')
  for (const file of files) form.append('files', file, file.name)
  try {
    const response = await authFetch(
      new Request(new URL('/api/v1/booklets', window.location.origin), {
        method: 'POST',
        body: form,
      }),
    )
    const body: unknown = await response.json().catch(() => undefined)
    if (response.ok) return { ok: true, booklet: body as BookletOut }
    if (response.status === 409 && typeof body === 'object' && body && 'duplicate_of' in body) {
      const { duplicate_of } = body as { duplicate_of: unknown }
      return {
        ok: false,
        kind: 'duplicate',
        message: problemOf(body, 'This file was uploaded before.').message,
        duplicateOf: Array.isArray(duplicate_of) ? duplicate_of.map(String) : [],
      }
    }
    const problem = problemOf(body, REFUSALS[response.status] ?? 'The upload failed.')
    return { ok: false, kind: 'error', message: [problem.message, ...problem.reasons].join(' ') }
  } catch {
    return { ok: false, kind: 'error', message: NETWORK_PROBLEM }
  }
}
