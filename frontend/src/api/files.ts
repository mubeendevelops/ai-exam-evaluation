import { authFetch } from './client'
import { NETWORK_PROBLEM, problemOf } from './errors'

export type UploadResult<T> = { ok: true; data: T } | { ok: false; message: string }

/**
 * Sends a file as the request body (the answer-key and diagram endpoints take the bytes
 * themselves, with the details in the query), through the same token handling as `api`.
 */
export async function uploadFile<T>(
  path: string,
  file: File,
  query: Record<string, string | boolean | string[]>,
  fallback: string,
): Promise<UploadResult<T>> {
  const url = new URL(path, window.location.origin)
  for (const [name, value] of Object.entries(query)) {
    for (const item of Array.isArray(value) ? value : [value]) {
      url.searchParams.append(name, String(item))
    }
  }
  try {
    const response = await authFetch(
      new Request(url, {
        method: 'POST',
        body: file,
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      }),
    )
    const body: unknown = await response.json().catch(() => undefined)
    if (response.ok) return { ok: true, data: body as T }
    const problem = problemOf(body, fallback)
    return { ok: false, message: [problem.message, ...problem.reasons].join(' ') }
  } catch {
    return { ok: false, message: NETWORK_PROBLEM }
  }
}

/** Fetches a file the API serves behind the bearer token. */
export async function fetchBlob(path: string): Promise<Blob> {
  const response = await authFetch(new Request(new URL(path, window.location.origin)))
  if (!response.ok) throw new Error(`file unavailable (${response.status})`)
  return response.blob()
}

/** Saves a blob under a name (an anchor click: the token-protected file cannot be a plain link). */
export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const link = globalThis.document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}
