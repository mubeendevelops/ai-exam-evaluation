// src/app/Layout.tsx — the shell every authenticated screen renders inside:
// a header with role-conditional navigation, and an <Outlet /> for the page.
//
// The role check below is a CONVENIENCE — it hides links a teacher can't use
// so the nav isn't cluttered with dead ends, nothing more. It is not a
// permission boundary: that boundary is Postgres RLS plus
// api/deps/identity.py's require_role, enforced on every request regardless
// of what this component renders (CLAUDE_CONTEXT.md §6). Hiding a link here
// does not and must not stand in for the server refusing the request.
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";
import type { CurrentUser } from "../auth/auth";

interface NavItem {
  to: string;
  label: string;
  roles: Array<CurrentUser["role"]>;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/upload", label: "Upload", roles: ["teacher", "admin"] },
  { to: "/results", label: "Results", roles: ["teacher", "admin"] },
  { to: "/questions", label: "Question bank", roles: ["teacher", "admin"] },
  { to: "/papers", label: "Paper generation", roles: ["teacher", "admin"] },
  { to: "/classes", label: "Classes & students", roles: ["teacher", "admin"] },
];

function navLinkClasses({ isActive }: { isActive: boolean }): string {
  return [
    "rounded-md px-3 py-2 text-sm font-medium",
    isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  ].join(" ");
}

export function Layout() {
  const { user, logout } = useAuth();

  const visibleItems = NAV_ITEMS.filter((item) => user && item.roles.includes(user.role));

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-6">
            <span className="text-base font-semibold text-slate-900">AI Exam Evaluation</span>
            {visibleItems.length > 0 && (
              <nav aria-label="Main" className="flex flex-wrap gap-1">
                {visibleItems.map((item) => (
                  <NavLink key={item.to} to={item.to} className={navLinkClasses}>
                    {item.label}
                  </NavLink>
                ))}
              </nav>
            )}
          </div>

          {user && (
            <div className="flex items-center gap-3 text-sm text-slate-600">
              <span>{user.email}</span>
              <button
                type="button"
                onClick={() => {
                  void logout();
                }}
                className="rounded-md border border-slate-300 px-3 py-1.5 font-medium hover:bg-slate-100"
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
