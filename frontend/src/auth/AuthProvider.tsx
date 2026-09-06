// src/auth/AuthProvider.tsx — the React-facing wrapper around src/auth/auth.ts.
//
// Every screen reads the session through useAuth() and never touches
// src/auth/auth.ts directly. This is the "hydrate the session on load" piece
// the app shell needs: it calls GET /auth/me once on mount (only when this
// browser has something to try — see hasStoredSession), caches the result
// through TanStack Query, and keeps it in sync with auth.ts's own
// establish/teardown events (onSessionChange) so a forced logout from the
// 401 interceptor and an explicit "sign out" click update the same state.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api } from "../api/client";
import { describeApiError } from "../api/errors";
import * as auth from "./auth";
import type { CurrentUser } from "./auth";

const ME_QUERY_KEY = ["auth", "me"] as const;

interface AuthContextValue {
  user: CurrentUser | null;
  isLoading: boolean;
  errorMessage: string | null;
  login: (email: string, password: string) => Promise<CurrentUser>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

async function fetchCurrentUser(): Promise<CurrentUser> {
  const { data, error, response } = await api.GET("/api/v1/auth/me");
  if (error) {
    throw new Error(describeApiError(error, response).message);
  }
  return data;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  // Whether THIS browser has anything worth trying to hydrate from — an
  // access token already in memory or a refresh token in localStorage.
  // Re-derived whenever auth.ts reports a session change (login, logout, or
  // a forced logout from the 401 interceptor), not just on mount.
  const [authed, setAuthed] = useState(() => auth.hasStoredSession());

  useEffect(() => auth.onSessionChange(() => setAuthed(auth.hasStoredSession())), []);

  const {
    data: user,
    isLoading,
    error,
  } = useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: fetchCurrentUser,
    enabled: authed,
    retry: false,
    // The account behind a bearer token does not change on its own — only a
    // login, a logout, or a role change that takes effect on the NEXT
    // refresh (api/routers/auth.py::me's docstring) can move this, and all
    // three go through establishSession/teardownSession, which this
    // component reacts to directly. Nothing here benefits from a background
    // refetch.
    staleTime: Infinity,
  });

  async function login(email: string, password: string): Promise<CurrentUser> {
    const loggedInUser = await auth.login(email, password);
    queryClient.setQueryData(ME_QUERY_KEY, loggedInUser);
    setAuthed(true);
    return loggedInUser;
  }

  async function logout(): Promise<void> {
    await auth.logout();
    queryClient.removeQueries({ queryKey: ME_QUERY_KEY });
    setAuthed(false);
  }

  const value: AuthContextValue = {
    user: authed ? (user ?? null) : null,
    isLoading: authed && isLoading,
    errorMessage: error ? error.message : null,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth() must be used inside <AuthProvider>.");
  }
  return context;
}
