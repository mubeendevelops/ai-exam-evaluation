// src/routes/Login.tsx — email + password sign-in.
//
// Talks to the API only through useAuth().login() (src/auth/AuthProvider.tsx
// -> src/auth/auth.ts). Nothing here touches a token, a header, or the
// generated client directly.
import { useId, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { AuthError } from "../auth/auth";
import { useAuth } from "../auth/AuthProvider";

export function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  // Already signed in (e.g. followed a bookmarked /login link) — nothing to
  // do here, send them where they were headed.
  if (user) {
    const redirectTo = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={redirectTo} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage(null);
    setSubmitting(true);
    try {
      await login(email, password);
      const redirectTo = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(redirectTo, { replace: true });
    } catch (error) {
      setErrorMessage(
        error instanceof AuthError ? error.message : "We couldn't sign you in. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-slate-900">Sign in</h1>
        <p className="mt-1 text-sm text-slate-500">Use the email and password your college admin gave you.</p>

        <form className="mt-6 flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
          <div className="flex flex-col gap-1">
            <label htmlFor={emailId} className="text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id={emailId}
              name="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200"
              aria-describedby={errorMessage ? errorId : undefined}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor={passwordId} className="text-sm font-medium text-slate-700">
              Password
            </label>
            <input
              id={passwordId}
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200"
              aria-describedby={errorMessage ? errorId : undefined}
            />
          </div>

          {errorMessage && (
            <p id={errorId} role="alert" className="text-sm text-red-600">
              {errorMessage}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
