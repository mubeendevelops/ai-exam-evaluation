// src/routes/ResultDetail.tsx — the two-panel Results screen: a student's
// scanned page on the left, what the AI read and scored on the right, linked
// by one shared selection.
import { useEffect, useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import { useResult, type RegionDetail, type SignalDetail } from "../api/queries";
import { Empty } from "../components/Empty";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";
import { PageImagePanel } from "../features/results/detail/PageImagePanel";
import { RegionCard, type ScoredComponent } from "../features/results/detail/RegionCard";
import { ReviewPanel } from "../features/results/detail/ReviewPanel";
import { SelectionProvider, useSelection } from "../features/results/detail/SelectionContext";
import { byReadingOrder } from "../features/results/detail/helpers";
import { StatusBadge } from "../features/results/StatusBadge";

export function ResultDetail() {
  const { answerId } = useParams<{ answerId: string }>();
  const result = useResult(answerId);

  if (result.isLoading) return <Loading label="Loading this answer…" />;
  if (result.error) {
    const message = (result.error as Error).message;
    const isNotFound = /404|no such answer|no answer/i.test(message);
    return (
      <ErrorMessage
        message={isNotFound ? "This answer doesn't exist, or isn't in your college." : message}
        onRetry={isNotFound ? undefined : () => void result.refetch()}
      />
    );
  }
  if (!result.data) return null;

  return <ResultDetailBody report={result.data} />;
}

function ResultDetailBody({ report }: { report: NonNullable<ReturnType<typeof useResult>["data"]> }) {
  const regions = useMemo(() => [...report.regions.items].sort(byReadingOrder), [report.regions.items]);
  const regionsByPage = useMemo(() => {
    const map = new Map<number, RegionDetail[]>();
    for (const region of regions) {
      if (region.page_number == null) continue;
      const list = map.get(region.page_number) ?? [];
      list.push(region);
      map.set(region.page_number, list);
    }
    return map;
  }, [regions]);

  const components = report.components.items as unknown as ScoredComponent[];

  const mergedSliceByBlock = useMemo(() => {
    const map = new Map<string, string>();
    if (report.merged_text.available && report.merged_text.text) {
      for (const part of report.merged_text.parts) {
        map.set(part.block_id, report.merged_text.text.slice(part.offset, part.offset + part.length));
      }
    }
    return map;
  }, [report.merged_text]);

  const flaggedRegions = regions.filter((r) => r.needs_review);
  const unscoredRegions = regions.filter((r) => !r.scored && !r.failure);

  return (
    <SelectionProvider regions={regions}>
      <KeyboardRegionNav />
      <ScrollToSelectedRegion />
      <div className="flex flex-col gap-4">
        <Header report={report} flaggedCount={flaggedRegions.length} />

        {(flaggedRegions.length > 0 || unscoredRegions.length > 0 || report.failures.length > 0) && (
          <ReviewQueueBanner flaggedRegions={flaggedRegions} unscoredRegions={unscoredRegions} failures={report.failures} />
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="h-[75vh]">
            <PageImagePanel answerId={report.answer_id} pages={report.pages} regionsByPage={regionsByPage} />
          </div>

          <div className="flex h-[75vh] flex-col gap-4 overflow-y-auto pr-1">
            <ReviewPanel
              answerId={report.answer_id}
              status={report.status}
              marksMax={report.marks_max}
              evaluation={report.evaluation}
              finalMarks={report.final_marks}
              history={report.history}
              reviews={report.reviews}
            />

            <ConfidenceSummary confidence={report.confidence} />

            {report.signals && <SignalsSummary signals={report.signals} />}

            <div className="space-y-3">
              {regions.length === 0 && (
                <Empty title="No regions recorded" description="This answer has no persisted regions to show." />
              )}
              {regions.map((region) => (
                <RegionCard
                  key={region.block_id}
                  region={region}
                  component={region.component_index != null ? components[region.component_index] : undefined}
                  mergedSlice={mergedSliceByBlock.get(region.block_id)}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </SelectionProvider>
  );
}

function Header({
  report,
  flaggedCount,
}: {
  report: NonNullable<ReturnType<typeof useResult>["data"]>;
  flaggedCount: number;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <Link to="/results" className="text-xs text-slate-400 hover:text-slate-600">
          ← Back to results
        </Link>
        <h1 className="mt-1 text-lg font-semibold text-slate-900">
          {report.regions.items[0]?.question ?? "Answer"}
          {report.question_text ? ` — ${report.question_text}` : ""}
        </h1>
        <p className="text-sm text-slate-500">{report.marks_max != null ? `${report.marks_max} marks` : ""}</p>
      </div>
      <div className="flex items-center gap-2">
        {flaggedCount > 0 && (
          <span className="rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-700">
            {flaggedCount} region{flaggedCount > 1 ? "s" : ""} need your review
          </span>
        )}
        <StatusBadge status={report.status as never} />
      </div>
    </div>
  );
}

function ReviewQueueBanner({
  flaggedRegions,
  unscoredRegions,
  failures,
}: {
  flaggedRegions: RegionDetail[];
  unscoredRegions: RegionDetail[];
  failures: Record<string, unknown>[];
}) {
  const { select } = useSelection();
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
      <p className="font-semibold">This answer needs attention before it's final.</p>
      <ul className="mt-2 list-disc space-y-1 pl-5">
        {flaggedRegions.length > 0 && (
          <li>
            {flaggedRegions.length} region{flaggedRegions.length > 1 ? "s were" : " was"} flagged for review at
            ingestion.{" "}
            <button
              type="button"
              className="underline"
              onClick={() => select(flaggedRegions[0].block_id, { zoom: true })}
            >
              Jump to it
            </button>
          </li>
        )}
        {unscoredRegions.length > 0 && (
          <li>
            {unscoredRegions.length} region{unscoredRegions.length > 1 ? "s were" : " was"} not included in any
            score. This is about regions belonging to THIS answer that nothing routed to — regions the segmenter
            couldn't attach to any answer at all aren't tracked by this screen yet.
          </li>
        )}
        {failures.length > 0 && (
          <li>
            {failures.length} stage failure{failures.length > 1 ? "s" : ""} occurred while scoring this answer.
            Partial results are shown below rather than a blank page.
          </li>
        )}
      </ul>
    </div>
  );
}

function ConfidenceSummary({ confidence }: { confidence: NonNullable<ReturnType<typeof useResult>["data"]>["confidence"] }) {
  if (confidence.question === null || confidence.question === undefined) return null;
  const counts = confidence.region_counts;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-medium text-slate-700">Evaluation confidence</span>
        <span className="font-semibold text-slate-900">{Math.round(confidence.question * 100)}%</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        This is the WEAKEST part of the answer, not an average — a question's confidence is only as strong as its
        least-confident region, so one badly-read part pulls the whole number down rather than being smoothed away.
      </p>
      {counts && Object.keys(counts).length > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          {counts.evaluated ?? 0} of {counts.total ?? 0} region(s) scored
          {counts.failed ? `, ${counts.failed} failed` : ""}
          {counts.flagged ? `, ${counts.flagged} flagged` : ""}.
        </p>
      )}
    </div>
  );
}

function SignalsSummary({ signals }: { signals: NonNullable<ReturnType<typeof useResult>["data"]>["signals"] }) {
  if (!signals) return null;
  const entries = (Object.entries(signals) as [string, SignalDetail | null][]).filter(
    (entry): entry is [string, SignalDetail] => Boolean(entry[1]),
  );
  if (entries.length === 0) return null;
  return (
    <details className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <summary className="cursor-pointer font-medium text-slate-700">Overall signal breakdown</summary>
      <table className="mt-2 w-full text-left text-xs">
        <thead className="text-slate-400">
          <tr>
            <th className="pr-2 font-medium">Signal</th>
            <th className="pr-2 font-medium">Score</th>
            <th className="pr-2 font-medium">Weight</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, signal]) => (
            <tr key={name} className="border-t border-slate-100">
              <td className="py-1 pr-2 capitalize text-slate-700">{name}</td>
              <td className="py-1 pr-2 text-slate-800">
                {signal.available && signal.score !== null && signal.score !== undefined
                  ? `${Math.round(signal.score * 100)}%`
                  : "did not run"}
              </td>
              <td className="py-1 text-slate-800">{signal.weight != null ? signal.weight.toFixed(2) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** Clicking a region's overlay on the LEFT panel selects it; this is what
 * makes that selection visible on the RIGHT panel too — scrolling its card
 * into view. A no-op when the card is already on screen (the reverse
 * direction: clicking the card itself), so one effect covers both link
 * directions the spec calls for. */
function ScrollToSelectedRegion() {
  const { selectedBlockId } = useSelection();

  useEffect(() => {
    if (!selectedBlockId) return;
    document.getElementById(`region-card-${selectedBlockId}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [selectedBlockId]);

  return null;
}

/** Global "[" / "]" bindings for reading-order navigation between regions —
 * separate from the page-image panel's own arrow-key panning, which owns the
 * arrow keys while the viewport has focus. Ignored while typing in a form
 * field so it doesn't fight with the override comment box. */
function KeyboardRegionNav() {
  const { selectNext } = useSelection();

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (e.key === "]") {
        e.preventDefault();
        selectNext(1);
      } else if (e.key === "[") {
        e.preventDefault();
        selectNext(-1);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectNext]);

  return null;
}
