/**
 * The addresses of the question editor and the way back to the page that opened it (P35).
 * A paper slot opens `/sme/questions/new?subject=&marks=&return=/sme/papers/<id>&slot=<key>`
 * (or `/sme/questions/<id>?return=…` to edit the linked question); saving a new question sends
 * the SME back to `return?linked=<question>&slot=<key>`, where the designer links it.
 */

/** A path inside the SME portal, or null: `return` comes from the address bar, so anything
 * else (another site, a protocol-relative `//host`, a backslash trick) is not followed. */
export function safeReturn(raw: string | null): string | null {
  if (!raw || !/^\/sme(\/|\?|$)/.test(raw)) return null
  if (raw.includes('//') || raw.includes('\\')) return null
  if ([...raw].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)) return null
  return raw
}

/** What the editor carries between pages. */
export interface EditorContext {
  return?: string | null
  slot?: string | null
  subject?: string | null
  marks?: string | null
  from?: string | null
}

function query(context: EditorContext): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(context)) if (value) params.set(key, value)
  const text = params.toString()
  return text === '' ? '' : `?${text}`
}

/** `/sme/questions/new` or `/sme/questions/<id>` with the context. */
export function editorUrl(questionId: string | null, context: EditorContext = {}): string {
  return `/sme/questions/${questionId ?? 'new'}${query(context)}`
}

/** The page the editor returns to, with the question to link when a slot asked for one. */
export function returnUrl(back: string, linked?: { questionId: string; slot: string }): string {
  if (!linked) return back
  const [path = back, existing = ''] = back.split('?', 2)
  const params = new URLSearchParams(existing)
  params.set('linked', linked.questionId)
  params.set('slot', linked.slot)
  return `${path}?${params.toString()}`
}
