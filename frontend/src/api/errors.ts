/** Turns the API's error bodies into text for the user. */
export interface ApiProblem {
  message: string
  reasons: string[]
}

interface Detailed {
  detail?: unknown
  reasons?: unknown
}

export function problemOf(error: unknown, fallback: string): ApiProblem {
  if (typeof error === 'object' && error !== null) {
    const { detail, reasons } = error as Detailed
    const list = Array.isArray(reasons) ? reasons.filter((r) => typeof r === 'string') : []
    if (typeof detail === 'string') return { message: detail, reasons: list }
    // FastAPI validation errors: [{loc, msg, type}]
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (typeof d === 'object' && d && 'msg' in d ? String(d.msg) : ''))
        .filter(Boolean)
      if (msgs.length > 0) return { message: fallback, reasons: msgs }
    }
  }
  return { message: fallback, reasons: [] }
}

export const NETWORK_PROBLEM = 'Cannot reach the server. Check your connection and try again.'
