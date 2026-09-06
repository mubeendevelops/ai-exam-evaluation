// src/auth/RequireAuth.tsx — route guard. Mounted around every authenticated
// route (see src/app/routes.tsx) so no protected screen renders so much as
// its first frame before the session is known.
//
// This is a CONVENIENCE, same as the role-conditional nav in the layout: the
// real permission boundary is Postgres RLS plus api/deps/identity.py's
// require_role checks (CLAUDE_CONTEXT.md §6). A client-side guard cannot be
// the security boundary — it exists so a signed-out teacher sees a login
// form instead of a screen that will 401 out from under them.
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { useAuth } from "./AuthProvider";

export function RequireAuth() {
  const { user, isLoading, errorMessage } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return <Loading label="Checking your session…" />;
  }

  // A genuine 401 here is already handled by auth.ts's interceptor (refresh,
  // then a hard redirect to /login on failure) before this ever renders. An
  // errorMessage surfacing here means something else went wrong (e.g. a 500
  // from /auth/me) — worth showing rather than silently treating as "signed
  // out", which would send a teacher back to a login form for no reason they
  // can see.
  if (errorMessage) {
    return <ErrorMessage message={errorMessage} onRetry={() => window.location.reload()} />;
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
