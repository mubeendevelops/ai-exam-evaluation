import { Navigate } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";

// Lands the signed-in user somewhere useful for their role. Teachers and
// admins go to Upload (the start of the day-to-day workflow); a
// platform_admin has no college-scoped screens to land on yet — see
// api/deps/identity.py::require_college_user's docstring on why those don't
// exist here — so they get a plain landing message instead of a redirect
// into a screen that isn't for them.
export function Home() {
  const { user } = useAuth();

  if (user && user.role !== "platform_admin") {
    return <Navigate to="/upload" replace />;
  }

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Welcome{user ? `, ${user.email}` : ""}</h1>
      <p className="mt-2 text-sm text-slate-600">
        Platform administration screens aren't built yet. Sign in as a teacher or admin account to use the app.
      </p>
    </div>
  );
}
