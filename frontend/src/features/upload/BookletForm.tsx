// src/features/upload/BookletForm.tsx — pick exam, student, paper for a
// booklet that has already been uploaded (or is about to be).
//
// Three server round trips, deliberately not client-side joined: exams and
// papers are independent lists, and students are filtered by exam_id because
// GET /api/v1/students only returns students who already have an answer on
// that exam (there is no direct student->exam link in this schema — §7C).
// paper_id is required by POST /evaluate today (no exams.paper_id FK yet), so
// a teacher has to name the paper the booklet's question markers ('Q1',
// 'Q2a') should resolve against.
import { useEffect } from "react";

import { useExams, usePapers, useStudents } from "../../api/queries";
import { Loading } from "../../components/Loading";

export interface BookletSelection {
  examId: string;
  studentId: string;
  paperId: string;
}

interface BookletFormProps {
  value: Partial<BookletSelection>;
  onChange: (value: Partial<BookletSelection>) => void;
  disabled?: boolean;
}

export function BookletForm({ value, onChange, disabled }: BookletFormProps) {
  const exams = useExams();
  const students = useStudents(value.examId);
  const papers = usePapers();

  // Changing the exam invalidates whatever student was picked under the
  // PREVIOUS exam's filtered list — a stale studentId here would let
  // POST /evaluate be called with a student/exam pair the picker never
  // actually offered together.
  useEffect(() => {
    if (value.studentId) onChange({ ...value, studentId: undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.examId]);

  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <Field label="Exam">
        {exams.isLoading ? (
          <Loading label="Loading exams…" />
        ) : exams.error ? (
          <p className="text-sm text-red-600">{(exams.error as Error).message}</p>
        ) : (
          <select
            className="select"
            value={value.examId ?? ""}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, examId: e.target.value || undefined })}
          >
            <option value="">Select an exam…</option>
            {exams.data?.items.map((exam) => (
              <option key={exam.exam_id} value={exam.exam_id}>
                {exam.name}
              </option>
            ))}
          </select>
        )}
      </Field>

      <Field label="Student">
        {!value.examId ? (
          <p className="pt-2 text-sm text-slate-400">Pick an exam first.</p>
        ) : students.isLoading ? (
          <Loading label="Loading students…" />
        ) : students.error ? (
          <p className="text-sm text-red-600">{(students.error as Error).message}</p>
        ) : students.data && students.data.items.length === 0 ? (
          <p className="pt-2 text-sm text-slate-400">No students found for this exam.</p>
        ) : (
          <select
            className="select"
            value={value.studentId ?? ""}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, studentId: e.target.value || undefined })}
          >
            <option value="">Select a student…</option>
            {students.data?.items.map((student) => (
              <option key={student.student_id} value={student.student_id}>
                {student.name} ({student.roll_number})
              </option>
            ))}
          </select>
        )}
      </Field>

      <Field label="Paper">
        {papers.isLoading ? (
          <Loading label="Loading papers…" />
        ) : papers.error ? (
          <p className="text-sm text-red-600">{(papers.error as Error).message}</p>
        ) : (
          <select
            className="select"
            value={value.paperId ?? ""}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, paperId: e.target.value || undefined })}
          >
            <option value="">Select a paper…</option>
            {papers.data?.items.map((paper) => (
              <option key={paper.paper_id} value={paper.paper_id}>
                {paper.name}
              </option>
            ))}
          </select>
        )}
      </Field>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}
