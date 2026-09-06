// src/api/client.ts — the one openapi-fetch instance the whole app shares.
//
// This file knows nothing about auth: it has no bearer header, no refresh
// logic, no redirect-to-login. Those live in src/auth/auth.ts, which wraps
// `api` below with middleware. Every other module imports the WRAPPED client
// from there, never this file directly — see that module's docstring.
import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Same-origin in production (the SPA is served by/behind the API's origin);
// override for local dev against a different host/port via .env.local.
const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export const api = createClient<paths>({ baseUrl });
