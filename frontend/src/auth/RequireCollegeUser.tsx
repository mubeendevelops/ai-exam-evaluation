// src/auth/RequireCollegeUser.tsx — second-layer route guard for the
// college-scoped screens (Upload, Results, Question bank, Paper generation,
// Classes & students).
//
// Mirrors api/deps/identity.py::require_college_user (= require_role("teacher",
// "admin")) on the client: a platform_admin has no college by CHECK
// constraint, and every one of these endpoints independently 403s that role
// (CLAUDE_CONTEXT.md §11). Without this guard a platform_admin who typed one
// of these URLs directly would see the screen render and then fill up with
// "Request failed (403)" fragments from every widget on it, instead of the
// one plain message Home already gives that role.
//
// SAME CAVEAT AS RequireAuth: this is a CONVENIENCE, not the permission
// boundary. It exists so a platform_admin sees a sensible redirect instead of
// a broken page — the backend's require_role rejects the underlying request
// regardless of whether this component runs at all.
import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "./AuthProvider";

export function RequireCollegeUser() {
  const { user } = useAuth();

  if (user && user.role === "platform_admin") {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
