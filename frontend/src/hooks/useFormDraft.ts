// src/hooks/useFormDraft.ts — persists an in-progress form's values to
// sessionStorage as they change, and restores them once on mount.
//
// WHY THIS EXISTS: the 401 interceptor (src/auth/auth.ts) redirects to
// /login with a hard `window.location.assign` when a dead refresh token
// means a request cannot be retried. That unmounts every component on the
// page — a teacher mid-way through typing an override, a review comment, or
// similar loses that input the instant their session happens to expire,
// with nothing telling them it happened. sessionStorage survives that
// navigation (and back again) within the same tab, so persisting on every
// keystroke and restoring on mount means the draft is still there when they
// log back in and return to the same screen — independent of WHAT
// interrupted them (session expiry, an accidental back button, a refresh).
//
// This hook has no opinion on the shape of the draft or on network
// requests — it only ever touches sessionStorage. Callers are responsible
// for calling `clear()` once the form is actually submitted; "the fields are
// empty" and "this just got submitted" are different states this hook
// cannot tell apart on its own.
import { useState } from "react";

function readDraft<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    // Private-browsing modes and locked-down storage settings can make
    // sessionStorage throw on access. Treated as "no draft to restore" —
    // the form just starts empty in that browser, same as it always did
    // before this hook existed.
    return null;
  }
}

function writeDraft<T>(key: string, value: T): void {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Best-effort; see readDraft.
  }
}

function clearDraft(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // Best-effort; see readDraft.
  }
}

/**
 * `key` must be unique per form INSTANCE (fold in the record id, e.g.
 * `ai-eval.override-draft:${answerId}`) so two different records' drafts
 * never collide, and stable across re-renders of the same form.
 *
 * `draft` is read ONCE, on mount (via useState's lazy initializer) — it is
 * the value to seed the form's own state from, not a live-updating value.
 * `save` should be called from the form's own onChange handlers with the
 * full current value; `clear` should be called once the form submits
 * successfully.
 */
export function useFormDraft<T>(key: string) {
  const [draft] = useState<T | null>(() => readDraft<T>(key));

  return {
    draft,
    save: (value: T) => writeDraft(key, value),
    clear: () => clearDraft(key),
  };
}
