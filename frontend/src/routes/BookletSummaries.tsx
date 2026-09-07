// src/routes/BookletSummaries.tsx — one row per (student, exam) "booklet",
// rolled up from GET /api/v1/results/booklets.
//
// The per-answer Results table (Results.tsx) is one row per question, which
// is the right grain for reviewing/overriding one answer but reads as "one
// booklet, many rows" the moment a student has more than one question. This
// screen is the other altitude: one row per booklet — it's what Upload's
// "View results" link lands on post-evaluation (`/results/summary?exam_id=
// &student_id=`, read from the URL below) — with a link per row into the
// filtered per-question Results table (`/results?exam_id=&student_id=`) for
// the breakdown.
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useBookletSummaries, useExams, useStudents } from "../api/queries";
import { Empty } from "../components/Empty";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { NeedsReviewBadge, ScoreValue, StatusBadge } from "../features/results/StatusBadge";

const PAGE_SIZE = 50;

export function BookletSummaries() {
  // exam_id/student_id/needs_review live in the URL, not local state — same
  // contract as Results.tsx, so a link here (Upload's "View results") can
  // arrive pre-filtered to the booklet that was just evaluated. offset stays
  // local: pagination position isn't worth carrying across navigations.
  const [params, setParams] = useSearchParams();
  const examId = params.get("exam_id") ?? undefined;
  const studentId = params.get("student_id") ?? undefined;
  const needsReviewOnly = params.get("needs_review") === "true";
  const [offset, setOffset] = useState(0);

  const exams = useExams();
  const students = useStudents(examId);
  const booklets = useBookletSummaries(
    { exam_id: examId, student_id: studentId, needs_review: needsReviewOnly || undefined },
    { limit: PAGE_SIZE, offset },
  );

  const examNames = new Map(exams.data?.items.map((e) => [e.exam_id, e.name]) ?? []);
  const studentNames = new Map(students.data?.items.map((s) => [s.student_id, s.name]) ?? []);

  function setParam(key: string, value: string | undefined) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "exam_id") next.delete("student_id");
    setParams(next, { replace: true });
    setOffset(0);
  }

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Booklets</h1>
      <p className="mt-1 text-sm text-slate-500">
        One row per student's whole exam. Open a booklet to review its individual answers.
      </p>

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
        {booklets.isLoading && <Loading label="Loading booklets…" />}
        {booklets.error && (
          <ErrorMessage message={(booklets.error as Error).message} onRetry={() => void booklets.refetch()} />
        )}
        {booklets.data && booklets.data.items.length === 0 && (
          <Empty title="No booklets match these filters" description="Try clearing a filter, or upload a booklet to evaluate." />
        )}
        {booklets.data && booklets.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-2 font-medium">Student</th>
                    <th className="px-4 py-2 font-medium">Exam</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Answered</th>
                    <th className="px-4 py-2 font-medium">Score</th>
                    <th className="px-4 py-2 font-medium">Submitted</th>
                    <th className="px-4 py-2 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {booklets.data.items.map((row) => (
                    <tr
                      key={`${row.student_id}:${row.exam_id}`}
                      className={`border-b border-slate-100 last:border-0 hover:bg-slate-50 ${row.needs_review ? "bg-red-50/40" : ""}`}
                    >
                      <td className="px-4 py-2 text-slate-900">
                        {studentNames.get(row.student_id) ?? row.student_id.slice(0, 8)}
                      </td>
                      <td className="px-4 py-2 text-slate-600">{examNames.get(row.exam_id) ?? row.exam_id.slice(0, 8)}</td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          <StatusBadge status={row.status} />
                          {row.needs_review && <NeedsReviewBadge />}
                        </div>
                      </td>
                      <td className="px-4 py-2 text-slate-600">
                        {row.scored_count} / {row.answer_count} scored
                      </td>
                      <td className="px-4 py-2">
                        <ScoreValue score={row.total_score} marksMax={row.max_score} />
                      </td>
                      <td className="px-4 py-2 text-slate-500">{new Date(row.latest_submitted_at).toLocaleString()}</td>
                      <td className="px-4 py-2 text-right">
                        <Link
                          to={`/results?exam_id=${row.exam_id}&student_id=${row.student_id}`}
                          className="text-slate-900 hover:underline"
                        >
                          View answers
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-slate-500">
              <span>
                Showing {offset + 1}–{offset + booklets.data.items.length} of {booklets.data.total}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={offset + booklets.data.items.length >= booklets.data.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
