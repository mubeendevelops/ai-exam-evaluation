// src/routes/QuestionBank.tsx — browse the shared question bank, generate
// drafts from content, and push them through the two mandatory review gates.
//
// A question is answerable by students only at status='live', and live is
// reachable only through two separate gates (CLAUDE_CONTEXT.md §11):
//   generate -> draft --Gate 1 (confirm)--> confirmed --Gate 2--> live
//                  \---Gate 1 (reject)---> rejected
// This screen never offers a shortcut across both gates in one click.
import { useState } from "react";

import { useQuestionsList, usePapers, type QuestionsFilter, type QuestionSummary } from "../api/queries";
import { Empty } from "../components/Empty";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { SharedBankNotice } from "../components/SharedBankNotice";
import { GenerateQuestionsForm } from "../features/questions/GenerateQuestionsForm";
import { QuestionDetailPanel } from "../features/questions/QuestionDetailPanel";
import { QuestionStatusBadge } from "../features/questions/QuestionStatusBadge";

const STATUS_OPTIONS: NonNullable<QuestionsFilter["status"]>[] = ["draft", "confirmed", "rejected", "live", "superseded"];
const STYLE_OPTIONS: NonNullable<QuestionsFilter["style"]>[] = ["long", "short", "one_word", "mcq"];
const SOURCE_OPTIONS: NonNullable<QuestionsFilter["source_type"]>[] = [
  "sentence",
  "paragraph",
  "diagram",
  "table",
  "formula",
  "manual",
];

const PAGE_SIZE = 25;

export function QuestionBank() {
  const [filter, setFilter] = useState<QuestionsFilter>({});
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | undefined>();

  const papers = usePapers();
  const questions = useQuestionsList(filter, { limit: PAGE_SIZE, offset });

  function updateFilter(next: QuestionsFilter) {
    setFilter(next);
    setOffset(0);
  }

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Question bank</h1>
      <p className="mt-1 text-sm text-slate-500">
        Generate draft questions from uploaded content, then take them through quality review and publication.
      </p>
      <div className="mt-3">
        <SharedBankNotice />
      </div>

      <div className="mt-6">
        <GenerateQuestionsForm
          onGenerated={() => {
            updateFilter({ ...filter, status: "draft" });
          }}
        />
      </div>

      <div className="mt-6 flex flex-wrap items-end gap-4">
        <FilterSelect
          label="Status"
          value={filter.status ?? ""}
          options={STATUS_OPTIONS}
          onChange={(v) => updateFilter({ ...filter, status: (v as QuestionsFilter["status"]) || undefined })}
        />
        <FilterSelect
          label="Style"
          value={filter.style ?? ""}
          options={STYLE_OPTIONS}
          onChange={(v) => updateFilter({ ...filter, style: (v as QuestionsFilter["style"]) || undefined })}
        />
        <FilterSelect
          label="Source"
          value={filter.source_type ?? ""}
          options={SOURCE_OPTIONS}
          onChange={(v) => updateFilter({ ...filter, source_type: (v as QuestionsFilter["source_type"]) || undefined })}
        />
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-slate-700">Origin</span>
          <select
            className="select"
            value={filter.is_ai_generated === undefined ? "" : String(filter.is_ai_generated)}
            onChange={(e) =>
              updateFilter({
                ...filter,
                is_ai_generated: e.target.value === "" ? undefined : e.target.value === "true",
              })
            }
          >
            <option value="">Any origin</option>
            <option value="true">AI-generated</option>
            <option value="false">Human-authored</option>
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-slate-700">Paper</span>
          <select
            className="select"
            value={filter.paper_id ?? ""}
            onChange={(e) => updateFilter({ ...filter, paper_id: e.target.value || undefined })}
          >
            <option value="">Any paper</option>
            {papers.data?.items.map((paper) => (
              <option key={paper.paper_id} value={paper.paper_id}>
                {paper.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_360px]">
        <div>
          {questions.isLoading && <Loading label="Loading questions…" />}
          {questions.error && (
            <ErrorMessage message={(questions.error as Error).message} onRetry={() => void questions.refetch()} />
          )}
          {questions.data && questions.data.questions.length === 0 && (
            <Empty
              title="No questions match these filters"
              description="Generate some from uploaded content, or clear a filter."
            />
          )}
          {questions.data && questions.data.questions.length > 0 && (
            <>
              <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-4 py-2 font-medium">Content</th>
                      <th className="px-4 py-2 font-medium">Style</th>
                      <th className="px-4 py-2 font-medium">Marks</th>
                      <th className="px-4 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {questions.data.questions.map((row) => (
                      <QuestionRow
                        key={row.question_id}
                        row={row}
                        selected={row.question_id === selectedId}
                        onSelect={() => setSelectedId(row.question_id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>

              <Pager
                total={questions.data.total}
                limit={PAGE_SIZE}
                offset={offset}
                onChange={setOffset}
                shown={questions.data.questions.length}
              />
            </>
          )}
        </div>

        {selectedId && <QuestionDetailPanel questionId={selectedId} onClose={() => setSelectedId(undefined)} />}
      </div>
    </div>
  );
}

function QuestionRow({
  row,
  selected,
  onSelect,
}: {
  row: QuestionSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${selected ? "bg-slate-100" : ""}`}
      onClick={onSelect}
    >
      <td className="max-w-[420px] px-4 py-2 text-slate-800">
        <span className="line-clamp-2">{row.content}</span>
      </td>
      <td className="px-4 py-2 text-slate-600">{row.style ?? "—"}</td>
      <td className="px-4 py-2 text-slate-600">{row.marks_max ?? "—"}</td>
      <td className="px-4 py-2">
        <QuestionStatusBadge status={row.status} />
      </td>
    </tr>
  );
}

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: T[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Any</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </label>
  );
}

function Pager({
  total,
  limit,
  offset,
  shown,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  shown: number;
  onChange: (offset: number) => void;
}) {
  return (
    <div className="mt-3 flex items-center justify-between text-sm text-slate-500">
      <span>
        Showing {offset + 1}–{offset + shown} of {total}
      </span>
      <div className="flex gap-2">
        <button type="button" className="btn-secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={offset + shown >= total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
