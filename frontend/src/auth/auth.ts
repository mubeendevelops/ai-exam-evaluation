// src/auth/auth.ts — THE ONLY FILE THAT KNOWS HOW THE FRONTEND HOLDS A SESSION.
//
// Mirrors api/deps/identity.py's rule for the backend: every other module
// asks THIS file for the current user or calls its login()/logout(), and
// none of them attach an Authorization header, read a token out of storage,
// or handle a 401 themselves. If a call site is doing any of those, it
// belongs here instead.
//
// ─────────────────────────────────────────────────────────────────────────
// TOKEN STORAGE: WHERE EACH TOKEN LIVES, AND WHY
// ─────────────────────────────────────────────────────────────────────────
//
// The API (api/schemas/auth.py) returns BOTH tokens as plain JSON fields on
// every login/refresh response — it never sets a cookie. There is no
// Set-Cookie anywhere in api/routers/auth.py, so an httpOnly refresh cookie
// is not actually an option here without a backend change; this module works
// with the API as it exists today.
//
//   access_token   Kept ONLY in memory (a module-level variable, below).
//                  Never written to localStorage/sessionStorage. It is a
//                  live bearer credential with no server-side row — anyone
//                  who has it can use it for up to 15 minutes with no way
//                  for the server to revoke it (api/deps/identity.py). Not
//                  persisting it means a page reload always drops it, and an
//                  XSS payload that runs once can only steal what is
//                  reachable at that instant, not everything storage has
//                  ever held.
//   refresh_token  Kept in localStorage. This is the deliberate tradeoff:
//                  localStorage is readable by any script running on the
//                  page, so an XSS bug can steal it and mint new sessions
//                  until it is rotated or revoked. We accept that because
//                  (a) the backend gives this SPA no cookie-based
//                  alternative, (b) unlike the access token this one DOES
//                  have a database row and can be killed at any time via
//                  POST /auth/logout (api/routers/auth.py), and (c) it is
//                  rotated on every use — a copy an attacker exfiltrates
//                  stops working the moment the legitimate client refreshes.
//
// Because neither token ever travels as a cookie, requests never set
// `credentials: "include"`, so there is nothing for CSRF protection to do
// here: every request is authenticated by a header a page must attach
// deliberately, which a cross-site form or <img> tag cannot do.
//
// ─────────────────────────────────────────────────────────────────────────
// THE 401 INTERCEPTOR
// ─────────────────────────────────────────────────────────────────────────
//
// Registered as openapi-fetch middleware on the shared `api` client
// (src/api/client.ts), so it runs for every request from every call site
// with no per-call wiring:
//
//   * onRequest attaches `Authorization: Bearer <access token>` when one is
//     held, and stashes an unconsumed clone of the request (keyed by
//     openapi-fetch's per-request `id`) in case it needs replaying.
//   * onResponse, on a 401 from anything other than /auth/login,
//     /auth/refresh or /auth/logout (those signal their OWN 401s — see
//     `isAuthEndpointNotSubjectToRefresh`, and retrying off one would recurse
//     through /auth/refresh forever): calls POST /auth/refresh exactly ONCE
//     (concurrent 401s share one in-flight refresh — see `refreshInFlight`),
//     then either replays the original request with the new access token, or,
//     if refresh failed (a dead/revoked refresh token), redirects to /login.
//     This is a single attempt, not a loop — a second 401 on the replay is
//     handed back to the caller rather than triggering another refresh.
//
// A page load with an expired-or-absent in-memory access token but a live
// refresh token in localStorage goes through exactly this path the first
// time anything calls the API (typically GET /auth/me): the request goes
// out with no/expired Authorization header, 401s, and the interceptor
// refreshes and replays it. That is the whole "hydrate the session on
// reload" mechanism — there is no separate bootstrap function.
import { api } from "../api/client";
import type { components } from "../api/schema";

export type CurrentUser = components["schemas"]["UserResponse"];
type TokenResponse = components["schemas"]["TokenResponse"];

const REFRESH_TOKEN_STORAGE_KEY = "ai-eval.refresh_token";

/** In-memory only — see the module docstring above. */
let accessToken: string | null = null;

/** De-dupes concurrent refresh attempts behind one shared promise. */
let refreshInFlight: Promise<boolean> | null = null;

/** Set once a redirect-to-login has been triggered, so a burst of 401s from
 * requests in flight at the same moment cannot fire it more than once. Reset
 * on every successful login. */
let redirectingToLogin = false;

type SessionListener = () => void;
const sessionListeners = new Set<SessionListener>();

/** Notified whenever the session is established or torn down — so
 * AuthProvider (src/auth/AuthProvider.tsx) can clear cached query state on a
 * forced logout that originates from the interceptor rather than a user
 * clicking "sign out". */
export function onSessionChange(listener: SessionListener): () => void {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

function notifySessionChange(): void {
  for (const listener of sessionListeners) listener();
}

function readStoredRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
  } catch {
    // Private-browsing modes and locked-down storage settings can make
    // localStorage throw on access. Treated the same as "no refresh token":
    // the user simply has to sign in every page load in that browser.
    return null;
  }
}

function writeStoredRefreshToken(token: string): void {
  try {
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, token);
  } catch {
    // Best-effort; see readStoredRefreshToken.
  }
}

function clearStoredRefreshToken(): void {
  try {
    localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
  } catch {
    // Best-effort; see readStoredRefreshToken.
  }
}

function establishSession(tokens: TokenResponse): void {
  accessToken = tokens.access_token;
  writeStoredRefreshToken(tokens.refresh_token);
  redirectingToLogin = false;
  notifySessionChange();
}

function teardownSession(): void {
  accessToken = null;
  clearStoredRefreshToken();
  notifySessionChange();
}

/** Whether this browser has anything worth trying to hydrate a session
 * from — used by AuthProvider to decide whether GET /auth/me is worth
 * calling at all, versus going straight to the login page. */
export function hasStoredSession(): boolean {
  return accessToken !== null || readStoredRefreshToken() !== null;
}

export class AuthError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "AuthError";
    this.status = status;
  }
}

export async function login(email: string, password: string): Promise<CurrentUser> {
  const { data, error, response } = await api.POST("/api/v1/auth/login", {
    body: { email, password },
  });
  if (error) {
    if (response.status === 429) {
      const retryAfter = response.headers.get("Retry-After");
      throw new AuthError(
        retryAfter
          ? `Too many sign-in attempts. Try again in ${retryAfter} seconds.`
          : "Too many sign-in attempts. Try again shortly.",
        429,
      );
    }
    throw new AuthError(
      typeof error.detail === "string" ? error.detail : "Incorrect email or password.",
      response.status,
    );
  }
  establishSession(data);
  return data.user;
}

/** Ends the session. Best-effort on the server side: if the network call
 * fails, local state is cleared anyway — nobody can use THIS browser as this
 * user afterwards, which is what logging out means from the user's seat,
 * even if the refresh token technically outlives the request that tried to
 * revoke it. */
export async function logout(options?: { allSessions?: boolean }): Promise<void> {
  const refreshToken = readStoredRefreshToken();
  if (refreshToken) {
    try {
      await api.POST("/api/v1/auth/logout", {
        body: { refresh_token: refreshToken, all_sessions: options?.allSessions ?? false },
      });
    } catch {
      // Network failure: fall through to clearing local state regardless.
    }
  }
  teardownSession();
}

async function performRefresh(): Promise<boolean> {
  const refreshToken = readStoredRefreshToken();
  if (!refreshToken) return false;

  try {
    const { data, error } = await api.POST("/api/v1/auth/refresh", {
      body: { refresh_token: refreshToken },
    });
    if (error || !data) {
      teardownSession();
      return false;
    }
    establishSession(data);
    return true;
  } catch {
    // Network failure mid-refresh: do not tear down a session over a
    // dropped connection, just fail this attempt. The next request that
    // 401s will try again.
    return false;
  }
}

/** Exactly one refresh in flight at a time, however many requests 401 at
 * once — see the module docstring's "THE 401 INTERCEPTOR" section. */
function refreshSessionOnce(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

const NO_REFRESH_PATH_SUFFIXES = [
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/logout",
];

function isAuthEndpointNotSubjectToRefresh(requestUrl: string): boolean {
  const path = new URL(requestUrl).pathname;
  return NO_REFRESH_PATH_SUFFIXES.some((suffix) => path.endsWith(suffix));
}

function redirectToLogin(): void {
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  teardownSession();
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

/** Clones stashed in onRequest (before the body is consumed by the actual
 * fetch), keyed by openapi-fetch's per-request id, so onResponse can replay
 * a request exactly once after a refresh. Always removed in onResponse and
 * onError, so this never grows across a session. */
const retryTemplates = new Map<string, Request>();

api.use({
  onRequest({ request, id }) {
    if (accessToken) {
      request.headers.set("Authorization", `Bearer ${accessToken}`);
    }
    retryTemplates.set(id, request.clone());
  },
  async onResponse({ request, response, id, options }) {
    const retryTemplate = retryTemplates.get(id);
    retryTemplates.delete(id);

    if (response.status !== 401 || isAuthEndpointNotSubjectToRefresh(request.url)) {
      return response;
    }

    const refreshed = await refreshSessionOnce();
    if (!refreshed || !retryTemplate) {
      redirectToLogin();
      return response;
    }

    retryTemplate.headers.set("Authorization", `Bearer ${accessToken}`);
    // NOT `options.fetch(retryTemplate)`: native fetch is a Window method
    // with a receiver check, and calling it AS A METHOD OF `options` (an
    // unrelated plain object openapi-fetch hands to middleware) fails that
    // check — "Failed to execute 'fetch' on 'Window': Illegal invocation".
    // A bare reference call has no receiver at all, which fetch tolerates
    // (this is exactly how openapi-fetch's own internals invoke it).
    const doFetch = options.fetch;
    const retried = await doFetch(retryTemplate);
    if (retried.status === 401) {
      redirectToLogin();
    }
    return retried;
  },
  onError({ id }) {
    retryTemplates.delete(id);
  },
});
