// src/features/results/detail/RegionCard.tsx — RIGHT PANEL: one persisted
// region, its text/structure, its confidence, and (expandable) why it scored
// what it did.
import { useState } from "react";

import type { RegionDetail } from "../../../api/queries";
import { useSelection } from "./SelectionContext";
import {
  CONFIDENCE_STYLES,
  confidenceLevel,
  formatConfidence,
  regionColor,
} from "./helpers";

export interface ScoredComponent {
  block_type: string;
  plugin?: string | null;
  plugin_version?: string | null;
  evaluator_model?: string | null;
  score: number;
  max_score?: number | null;
  confidence: number;
  block_ids?: string[];
  pages?: number[];
  merged_from?: number;
  extraction_confidence?: number;
  explanation?: string | null;
  primary?: boolean;
  signals?: Record<
    string,
    { score: number | null; model?: string | null; weight?: number | null; available?: boolean; explanation?: string | null }
  > | null;
  detail?: Record<string, unknown> | null;
}

interface RegionCardProps {
  region: RegionDetail;
  component: ScoredComponent | undefined;
  /** This region's slice of the merged text, when it is a merged text part
   * (§7D decision 3) — undefined when merged text isn't available or this
   * region stands alone. */
  mergedSlice: string | undefined;
}

export function RegionCard({ region, component, mergedSlice }: RegionCardProps) {
  const { selectedBlockId, select } = useSelection();
  const [showWhy, setShowWhy] = useState(false);
  const isSelected = region.block_id === selectedBlockId;
  const color = regionColor(region.block_type);

  return (
    <article
      id={`region-card-${region.block_id}`}
      tabIndex={0}
      onClick={() => select(region.block_id, { zoom: true })}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          select(region.block_id, { zoom: true });
        }
      }}
      className={[
        "cursor-pointer rounded-lg border p-4 outline-none transition-colors",
        isSelected ? "border-slate-900 ring-2 ring-slate-900/20" : "border-slate-200 hover:border-slate-300",
      ].join(" ")}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: color }} aria-hidden="true" />
        <span className="text-sm font-medium capitalize text-slate-900">{region.block_type}</span>
        {region.page_number != null && <span className="text-xs text-slate-400">page {region.page_number}</span>}
        {region.merged_with > 1 && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
            part {region.order_in_component! + 1} of {region.merged_with}
          </span>
        )}
        <div className="ml-auto flex gap-1.5">
          {region.needs_review && (
            <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">Needs review</span>
          )}
          <ConfidenceChip label="Classification" value={region.classification_confidence} />
          {region.ocr_confidence !== null && <ConfidenceChip label="OCR" value={region.ocr_confidence} />}
        </div>
      </header>

      {confidenceLevel(region.ocr_confidence ?? region.component_confidence) === "low" && (
        <p className="mt-2 text-xs font-medium text-amber-700">
          Low confidence — the AI may have misread this region. Compare it against the handwriting on the left.
        </p>
      )}

      <div className="mt-3 text-sm text-slate-700">
        <RegionBody region={region} component={component} mergedSlice={mergedSlice} />
      </div>

      {region.failure && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800">
          <p className="font-medium">
            {region.failure.stage as string}: {region.failure.error_type as string}
          </p>
          <p>{region.failure.message as string}</p>
        </div>
      )}

      {!region.scored && !region.failure && (
        <p className="mt-3 text-xs italic text-slate-400">Not included in this answer's score.</p>
      )}

      {component && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <button
            type="button"
            className="text-xs font-medium text-slate-500 hover:text-slate-800"
            onClick={(e) => {
              e.stopPropagation();
              setShowWhy((v) => !v);
            }}
          >
            {showWhy ? "Hide" : "Show"} why this scored {component.score}
            {component.max_score != null ? ` / ${component.max_score}` : ""}
          </button>
          {showWhy && <WhyPanel component={component} />}
        </div>
      )}
    </article>
  );
}

function RegionBody({ region, component, mergedSlice }: RegionCardProps) {
  if (region.block_type === "text") {
    if (mergedSlice !== undefined) {
      return <p className="whitespace-pre-wrap">{mergedSlice}</p>;
    }
    if (region.text) {
      return <p className="whitespace-pre-wrap">{region.text}</p>;
    }
    return <NoTextNotice region={region} />;
  }

  // Table / diagram: there is no prose to show — the structural summary is
  // the honest equivalent (CLAUDE_CONTEXT.md: "no readable OCR text in the
  // same sense").
  const detail = component?.detail;
  if (!detail) {
    return <p className="italic text-slate-400">No structural comparison recorded for this region.</p>;
  }
  return <StructuralDetail detail={detail} />;
}

function NoTextNotice({ region }: { region: RegionDetail }) {
  // `text_source` distinguishes two genuinely different facts, per
  // migrations/019_answer_block_extractions.sql's COMMENT ON COLUMN text:
  // "NULL is legitimate ... not the same as never having tried."
  //   - text_source is null / extraction_available is false: no
  //     answer_block_extractions row exists for this region at all — the
  //     booklet hasn't been (re-)evaluated since this region was ingested.
  //   - text_source === "answer_block_extractions" but text is still empty:
  //     extraction ran and read nothing back — a real result, not a gap.
  const message = region.extraction_available
    ? "OCR ran on this region and found no text — this appears to be a blank or unreadable region, not a missing read."
    : "This region hasn't been through text extraction yet — it will be OCR'd the next time this booklet is evaluated.";
  return <p className="italic text-slate-400">{message}</p>;
}

function StructuralDetail({ detail }: { detail: Record<string, unknown> }) {
  const rows: [string, string][] = [];
  if (typeof detail.structural_score === "number") rows.push(["Structure (rows/columns)", formatConfidence(detail.structural_score)]);
  if (typeof detail.content_score === "number") rows.push(["Content (values)", formatConfidence(detail.content_score)]);
  if (typeof detail.content_score_on_aligned === "number") {
    rows.push(["Content, aligned cells only", formatConfidence(detail.content_score_on_aligned)]);
  }
  if (typeof detail.overall_accuracy === "number") rows.push(["Overall accuracy", formatConfidence(detail.overall_accuracy)]);
  if (detail.verdict_counts && typeof detail.verdict_counts === "object") {
    for (const [k, v] of Object.entries(detail.verdict_counts as Record<string, unknown>)) {
      rows.push([`Cells: ${k}`, String(v)]);
    }
  }
  for (const key of ["missing_information_count", "anomalies_count", "missing_rows_count", "extra_rows_count"]) {
    if (typeof detail[key] === "number" && (detail[key] as number) > 0) {
      rows.push([key.replace(/_count$/, "").replace(/_/g, " "), String(detail[key])]);
    }
  }

  if (rows.length === 0) return <p className="italic text-slate-400">No structural detail recorded.</p>;

  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="capitalize text-slate-500">{label}</dt>
          <dd className="font-medium text-slate-800">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function WhyPanel({ component }: { component: ScoredComponent }) {
  return (
    <div className="mt-2 space-y-2 rounded-md bg-slate-50 p-3 text-xs">
      {component.explanation && <p className="text-slate-700">{component.explanation}</p>}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-slate-500">
        <dt>Plugin</dt>
        <dd className="text-slate-800">
          {component.plugin ?? "unknown"} {component.plugin_version ? `v${component.plugin_version}` : ""}
        </dd>
        {component.evaluator_model && (
          <>
            <dt>Model</dt>
            <dd className="text-slate-800">{component.evaluator_model}</dd>
          </>
        )}
        <dt>Confidence</dt>
        <dd className="text-slate-800">{formatConfidence(component.confidence)}</dd>
        {component.extraction_confidence !== undefined && (
          <>
            <dt>Extraction confidence</dt>
            <dd className="text-slate-800">{formatConfidence(component.extraction_confidence)}</dd>
          </>
        )}
      </dl>

      {component.signals && (
        <table className="mt-2 w-full text-left">
          <thead className="text-slate-400">
            <tr>
              <th className="pr-2 font-medium">Signal</th>
              <th className="pr-2 font-medium">Score</th>
              <th className="pr-2 font-medium">Weight</th>
              <th className="font-medium">Model</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(component.signals).map(([name, signal]) => (
              <tr key={name} className="border-t border-slate-200">
                <td className="py-1 pr-2 capitalize text-slate-700">{name}</td>
                <td className="py-1 pr-2 text-slate-800">
                  {signal.available && signal.score !== null ? formatConfidence(signal.score) : "did not run"}
                </td>
                <td className="py-1 pr-2 text-slate-800">
                  {signal.weight !== null && signal.weight !== undefined ? signal.weight.toFixed(2) : "—"}
                </td>
                <td className="py-1 text-slate-500">{signal.model ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ConfidenceChip({ label, value }: { label: string; value: number | null | undefined }) {
  const level = confidenceLevel(value);
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONFIDENCE_STYLES[level]}`} title={`${label} confidence`}>
      {formatConfidence(value)}
    </span>
  );
}
