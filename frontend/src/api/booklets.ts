import { authFetch } from './client'
import { NETWORK_PROBLEM, problemOf } from './errors'
import type { components } from './schema'

type BookletOut = components['schemas']['BookletOut']
export type UnitUploadOut = components['schemas']['UnitUploadOut']

export type Sent<T> = { ok: true; value: T } | { ok: false; message: string }

const REFUSALS: Record<number, string> = {
  413: 'The upload is too large. Send fewer or smaller files.',
  422: 'The upload was refused.',
}

/**
 * Multipart POST (the generated client does not send files): the form is built by the caller.
 */
async function postForm<T>(path: string, form: FormData, fallback: string): Promise<Sent<T>> {
  try {
    const response = await authFetch(
      new Request(new URL(path, window.location.origin), { method: 'POST', body: form }),
    )
    const body: unknown = await response.json().catch(() => undefined)
    if (response.ok) return { ok: true, value: body as T }
    const problem = problemOf(body, REFUSALS[response.status] ?? fallback)
    return { ok: false, message: [problem.message, ...problem.reasons].join(' ') }
  } catch {
    return { ok: false, message: NETWORK_PROBLEM }
  }
}

/**
 * Uploads booklets into a unit (P26): one PDF per student, each named by the roll number. The
 * answer lists every file with its outcome; refused files stored nothing.
 */
export function uploadToUnit(unitId: string, files: File[]): Promise<Sent<UnitUploadOut>> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  return postForm(`/api/v1/units/${unitId}/booklets`, form, 'The upload failed.')
}

/** A retake: the booklet (waiting for a retake, or failed) is replaced by the new PDF. */
export function replaceBooklet(bookletId: string, file: File): Promise<Sent<BookletOut>> {
  const form = new FormData()
  form.append('file', file, file.name)
  return postForm(`/api/v1/booklets/${bookletId}/replace`, form, 'The retake failed.')
}
