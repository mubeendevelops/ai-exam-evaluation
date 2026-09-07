// src/routes/Results.tsx — this college's answers, filterable, with
// needs-review answers surfaced first.
//
// One row per answer (GET /api/v1/results) — the full per-signal report with
// regions, ledger history and reviews lives behind GET /results/{answer_id},
// which clicking a row navigates to. Filters live in the URL so a link from
// the Booklets summary screen's per-row "View answers" (?exam_id=&student_id=)
// lands pre-filtered.
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useExams, useResultsList, useStudents, type ResultSummary } from "../api/queries";
import { Empty } from "../components/Empty";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { NeedsReviewBadge, ScoreValue, StatusBadge } from "../features/results/StatusBadge";

const STATUS_OPTIONS: ResultSummary["status"][] = [
  "pending_evaluation",
  "ai_scored",
  "sme_reviewed",
  "finalized",
  "flagged",
];

export function Results() {
  const [params, setParams] = useSearchParams();
  const examId = params.get("exam_id") ?? undefined;
  const studentId = params.get("student_id") ?? undefined;
  const status = (params.get("status") as ResultSummary["status"] | null) ?? undefined;
  const needsReviewOnly = params.get("needs_review") === "true";

  const exams = useExams();
  const students = useStudents(examId);
  const results = useResultsList({ exam_id: examId, student_id: studentId, status, needs_review: needsReviewOnly || undefined });

  const examNames = useMemo(() => new Map(exams.data?.items.map((e) => [e.exam_id, e.name]) ?? []), [exams.data]);
  const studentNames = useMemo(
    () => new Map(students.data?.items.map((s) => [s.student_id, s.name]) ?? []),
    [students.data],
  );

  function setParam(key: string, value: string | undefined) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "exam_id") next.delete("student_id");
    setParams(next, { replace: true });
  }

  // Needs-review answers surface at the top regardless of what order the
  // API returned them in (it orders by submitted_at, not by review state) —
  // "surface it at the top with a clear badge" is a UI-layer requirement,
  // not something to ask the list endpoint to do.
  const sorted = useMemo(() => {
    const items = results.data?.items ?? [];
    return [...items].sort((a, b) => {
      if (a.needs_review !== b.needs_review) return a.needs_review ? -1 : 1;
      return new Date(b.submitted_at).getTime() - new Date(a.submitted_at).getTime();
    });
  }, [results.data]);

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Results</h1>
      <p className="mt-1 text-sm text-slate-500">Review AI-scored answers, confirm or override them.</p>

      <div className="mt-6 flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-slate-700">Exam</span>
          <select className="select" value={examId ?? ""} onChange={(e) => setParam("exam_id", e.target.value || undefined)}>
            <option value="">All exams</option>
            {exams.data?.items.map((exam) => (
              <option key={exam.exam_id} value={exam.exam_id}>
                {exam.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-slate-700">Student</span>
          <select
            className="select"
            value={studentId ?? ""}
            disabled={!examId}
            onChange={(e) => setParam("student_id", e.target.value || undefined)}
          >
            <option value="">All students</option>
            {students.data?.items.map((student) => (
              <option key={student.student_id} value={student.student_id}>
                {student.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-slate-700">Status</span>
          <select className="select" value={status ?? ""} onChange={(e) => setParam("status", e.target.value || undefined)}>
            <option value="">Any status</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 pb-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={needsReviewOnly}
            onChange={(e) => setParam("needs_review", e.target.checked ? "true" : undefined)}
          />
          Needs review only
        </label>
      </div>

      <div className="mt-6">
        {results.isLoading && <Loading label="Loading results…" />}
        {results.error && <ErrorMessage message={(results.error as Error).message} onRetry={() => void results.refetch()} />}
        {results.data && sorted.length === 0 && (
          <Empty title="No results match these filters" description="Try clearing a filter, or upload a booklet to evaluate." />
        )}
        {results.data && sorted.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Review</th>
                  <th className="px-4 py-2 font-medium">Student</th>
                  <th className="px-4 py-2 font-medium">Exam</th>
                  <th className="px-4 py-2 font-medium">Question</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Score</th>
                  <th className="px-4 py-2 font-medium">Submitted</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr
                    key={row.answer_id}
                    className={`border-b border-slate-100 last:border-0 hover:bg-slate-50 ${row.needs_review ? "bg-red-50/40" : ""}`}
                  >
                    <td className="px-4 py-2">{row.needs_review && <NeedsReviewBadge />}</td>
                    <td className="px-4 py-2">
                      <Link to={`/results/${row.answer_id}`} className="text-slate-900 hover:underline">
                        {studentNames.get(row.student_id) ?? row.student_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-slate-600">{examNames.get(row.exam_id) ?? row.exam_id.slice(0, 8)}</td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-500">{row.question_id.slice(0, 8)}</td>
                    <td className="px-4 py-2">
                      <StatusBadge status={row.status} />
                    </td>
                    <td className="px-4 py-2">
                      <ScoreValue score={row.score} />
                    </td>
                    <td className="px-4 py-2 text-slate-500">{new Date(row.submitted_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
