/**
 * The unsaved form of a paper, kept in session storage while the SME is in the question editor
 * (P35) so that coming back finds the designer as it was left. One draft per paper, for the
 * version it was made on; reading it does not remove it (the caller clears it once restored).
 */
import type { FormState } from './blueprint'

const PREFIX = 'tarn.paper-draft.'

interface Draft {
  version: number
  form: FormState
}

export function saveDraft(paperId: string, version: number, form: FormState): void {
  try {
    globalThis.sessionStorage.setItem(PREFIX + paperId, JSON.stringify({ version, form }))
  } catch {
    // Storage blocked or full: the SME loses the unsaved edits, nothing else.
  }
}

/** The draft made on this version of the paper, or null. */
export function loadDraft(paperId: string, version: number): FormState | null {
  try {
    const raw = globalThis.sessionStorage.getItem(PREFIX + paperId)
    if (!raw) return null
    const draft = JSON.parse(raw) as Partial<Draft>
    return draft.version === version && draft.form ? draft.form : null
  } catch {
    return null
  }
}

export function clearDraft(paperId: string): void {
  try {
    globalThis.sessionStorage.removeItem(PREFIX + paperId)
  } catch {
    // Nothing to forget.
  }
}
