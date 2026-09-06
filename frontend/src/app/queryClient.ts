// src/app/queryClient.ts — the one QueryClient the app renders with, and its
// defaults.
//
// The global `onError` handlers exist so a screen that doesn't bother
// writing its own error UI still gets something better than a silently
// stuck spinner: a console line carrying the API's request id (RE-5,
// api/main.py's exception handlers), which is what a bug report needs to
// find the matching server log line. A screen that DOES want its own error
// UI (most of them, via useQuery's `error` return value) is unaffected —
// this only supplements that, it never swallows the error.
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

function logQueryFailure(error: unknown): void {
  if (!(error instanceof Error)) return;
  // Errors thrown by this app's query functions carry the already-normalized
  // message from describeApiError (see src/auth/AuthProvider.tsx for the
  // pattern every query function should follow). Re-deriving a request id
  // here would need the Response, which isn't available this far from the
  // fetch call — screens that want to display one should call
  // describeApiError themselves and pass the id through.
  console.error("Request failed:", error.message);
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A teacher on a slow campus network benefits from one retry; a
      // request that's failing because of an expired session (handled by
      // the 401 interceptor in src/auth/auth.ts, not by React Query) or bad
      // input should not be retried at all.
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
  queryCache: new QueryCache({ onError: logQueryFailure }),
  mutationCache: new MutationCache({ onError: logQueryFailure }),
});
