// src/components/SharedBankNotice.tsx — the caveat every question-bank and
// paper-generation screen must carry.
//
// The question schema's 12 tables (and the paper tables) carry no college_id
// and are not under RLS — a stated design decision, not an oversight
// (CLAUDE_CONTEXT.md §11: "The question bank is SHARED — there is nothing to
// isolate there"). A question or paper visible here can be, and often is,
// used by another college. Never render this bank as if it belonged to the
// logged-in college, and never add a college filter to it.
export function SharedBankNotice() {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-600">
      <span className="font-semibold text-slate-700">Shared across colleges. </span>
      Questions and papers here aren't scoped to your college — everyone drawing from this bank sees the same
      rows, by design. There's no "your college's questions" filter because that isn't how this data is modeled.
    </div>
  );
}
