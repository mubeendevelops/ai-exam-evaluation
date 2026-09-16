// src/features/content/ParagraphPicker.tsx — the list half of the content
// picker: filter by document/status/search, page through results, pick one.
//
// Reused in two shapes: full-page (Content.tsx, every filter visible) and
// compact (GenerateQuestionsForm.tsx, `fixedStatus="active"` hides the
// status filter — generating from a superseded paragraph is refused
// server-side anyway, so there is no reason to let a teacher pick one here).
import { useState } from "react";

import {
  useDocuments,
  useParagraphs,
  type ParagraphsFilter,
  type ParagraphSummary,
} from "../../api/queries";
import { Empty } from "../../components/Empty";
import { ErrorMessage } from "../../components/ErrorMessage";
import { Loading } from "../../components/Loading";
import { ParagraphStatusBadge } from "./ParagraphStatusBadge";

const STATUS_OPTIONS: NonNullable<ParagraphsFilter["status"]>[] = ["active", "superseded"];

interface ParagraphPickerProps {
  /** Locks the status filter to one value and hides the control — see the
   * module docstring. */
  fixedStatus?: ParagraphsFilter["status"];
  selectedId?: string;
  onSelect?: (paragraph: ParagraphSummary) => void;
  pageSize?: number;
}

export function ParagraphPicker({ fixedStatus, selectedId, onSelect, pageSize = 25 }: ParagraphPickerProps) {
  const [filter, setFilter] = useState<ParagraphsFilter>(fixedStatus ? { status: fixedStatus } : {});
  const [offset, setOffset] = useState(0);
  const [searchInput, setSearchInput] = useState("");

  const documents = useDocuments();
  const paragraphs = useParagraphs(filter, { limit: pageSize, offset });

  function updateFilter(next: ParagraphsFilter) {
    setFilter(next);
    setOffset(0);
  }

  function submitSearch(e: React.FormEvent) {
    e.preventDefault();
    updateFilter({ ...filter, q: searchInput.trim() || undefined });
  }

  return (
    <div>
      <div className="flex flex-wrap items-end gap-4">
        {!fixedStatus && (
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-slate-700">Status</span>
            <select
              className="select"
              value={filter.status ?? ""}
              onChange={(e) =>
                updateFilter({ ...filter, status: (e.target.value as ParagraphsFilter["status"]) || undefined })
              }
            >
              <option value="">Any</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-slate-700">Document</span>
          <select
            className="select"
            value={filter.source_document ?? ""}
            onChange={(e) => updateFilter({ ...filter, source_document: e.target.value || undefined })}
          >
            <option value="">Any document</option>
            {documents.data?.map((d) => (
              <option key={d.source_document} value={d.source_document}>
                {d.source_document} ({d.paragraph_count})
              </option>
            ))}
          </select>
        </label>

        <form onSubmit={submitSearch} className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-slate-700">Search</span>
          <div className="flex gap-2">
            <input
              className="select"
              placeholder="Search content…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
            <button type="submit" className="btn-secondary">
              Search
            </button>
          </div>
        </form>
      </div>

      <div className="mt-4">
        {paragraphs.isLoading && <Loading label="Loading content…" />}
        {paragraphs.error && (
          <ErrorMessage message={(paragraphs.error as Error).message} onRetry={() => void paragraphs.refetch()} />
        )}
        {paragraphs.data && paragraphs.data.items.length === 0 && (
          <Empty
            title="No content matches these filters"
            description="Upload some on the left, or clear a filter."
          />
        )}
        {paragraphs.data && paragraphs.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
              <table className="w-full min-w-[560px] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-2 font-medium">Content</th>
                    <th className="px-4 py-2 font-medium">Document</th>
                    <th className="px-4 py-2 font-medium">Sentences</th>
                    <th className="px-4 py-2 font-medium">Questions</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {paragraphs.data.items.map((row) => (
                    <ParagraphRow
                      key={row.paragraph_id}
                      row={row}
                      selected={row.paragraph_id === selectedId}
                      onSelect={() => onSelect?.(row)}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            <Pager
              total={paragraphs.data.total}
              limit={pageSize}
              offset={offset}
              shown={paragraphs.data.items.length}
              onChange={setOffset}
            />
          </>
        )}
      </div>
    </div>
  );
}

function ParagraphRow({
  row,
  selected,
  onSelect,
}: {
  row: ParagraphSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${selected ? "bg-slate-100" : ""}`}
      onClick={onSelect}
    >
      <td className="max-w-[420px] px-4 py-2 text-slate-800">
        <span className="line-clamp-2">{row.preview}</span>
      </td>
      <td className="max-w-[160px] px-4 py-2 text-slate-600">
        <span className="line-clamp-1">{row.source_document}</span>
      </td>
      <td className="px-4 py-2 text-slate-600">{row.sentence_count}</td>
      <td className="px-4 py-2 text-slate-600">{row.question_count}</td>
      <td className="px-4 py-2">
        <ParagraphStatusBadge status={row.status} />
      </td>
    </tr>
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
        <button
          type="button"
          className="btn-secondary"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
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
