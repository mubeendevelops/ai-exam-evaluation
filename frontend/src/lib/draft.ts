import type { FormState } from './blueprint'

const KEY = 'tarn.schema-designer.draft'
const VERSION = 1

/**
 * The form is kept in the browser's session storage so a trip to another tab does not lose it.
 * It is a convenience only: every read and write is guarded, because storage can be blocked or
 * empty (private windows), and a draft of another shape is ignored.
 */
export function loadDraft(): FormState | null {
  try {
    const raw = globalThis.sessionStorage.getItem(KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      (parsed as { version?: unknown }).version === VERSION &&
      Array.isArray((parsed as { form?: { sections?: unknown } }).form?.sections)
    ) {
      return (parsed as { form: FormState }).form
    }
  } catch {
    // unreadable draft: start fresh
  }
  return null
}

export function saveDraft(form: FormState): void {
  try {
    globalThis.sessionStorage.setItem(KEY, JSON.stringify({ version: VERSION, form }))
  } catch {
    // storage unavailable: the draft just lives as long as the page
  }
}

export function clearDraft(): void {
  try {
    globalThis.sessionStorage.removeItem(KEY)
  } catch {
    // nothing to clear
  }
}
