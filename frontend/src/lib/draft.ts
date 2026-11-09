import { initialForm, type FormState } from './blueprint'

const KEY = 'tarn.schema-designer.draft'
const VERSION = 2
/** Version 1 drafts (before P33) lack the 1.2 fields: they are filled with the defaults. */
const READABLE = [1, 2]

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
      READABLE.includes((parsed as { version?: number }).version ?? 0) &&
      Array.isArray((parsed as { form?: { sections?: unknown } }).form?.sections)
    ) {
      return { ...initialForm(), ...(parsed as { form: Partial<FormState> }).form }
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
