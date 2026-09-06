// src/api/errors.ts — turns whatever shape an API failure came back in into
// one thing every screen can display: a human message plus, when the server
// put one on the response, a request id (RE-5) worth handing to support.
//
// api/main.py's exception handlers give errors one of three shapes:
//   - FastAPI's default HTTPException body: {"detail": "..." | [...]}
//   - the tenant-context / unhandled-exception handlers:
//     {"error": "...", "detail": "...", "request_id": "..."}
//   - plain network failure: no response at all (fetch threw)
export interface ApiErrorInfo {
  message: string;
  requestId?: string;
}

function detailToMessage(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic 422 validation errors: a list of {loc, msg, type}.
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : null))
      .filter((msg): msg is string => Boolean(msg));
    if (messages.length > 0) return messages.join(" ");
  }
  return undefined;
}

export function describeApiError(error: unknown, response?: Response): ApiErrorInfo {
  const requestId = response?.headers.get("X-Request-ID") ?? undefined;

  if (error && typeof error === "object") {
    const body = error as Record<string, unknown>;
    const message = detailToMessage(body.detail) ?? (typeof body.error === "string" ? body.error : undefined);
    if (message) return { message, requestId: requestId ?? (typeof body.request_id === "string" ? body.request_id : undefined) };
  }

  if (response && !response.ok) {
    return { message: `Request failed (${response.status}).`, requestId };
  }

  return { message: "Something went wrong. Please try again.", requestId };
}
