// src/features/results/detail/helpers.ts — shared formatting/color helpers
// for the Result Detail screen. No API calls, no state — pure functions so
// every panel renders the same region the same way.
import type { RegionDetail } from "../../../api/queries";

/** Colour per block_type, chosen for contrast over dark scanned ink: each
 * overlay is drawn with a white halo behind a saturated stroke (see
 * PageImagePanel), so the hue mainly has to read well as a small swatch and
 * differ clearly from its neighbours. `formula` and anything unmapped fall
 * back to slate — no plugin scores formula regions today (CLAUDE_CONTEXT.md
 * §7C), so it is drawn but visually "other". */
export const REGION_COLORS: Record<string, string> = {
  text: "#2563eb", // blue-600
  table: "#059669", // emerald-600
  diagram: "#c026d3", // fuchsia-600
};

export function regionColor(blockType: string): string {
  return REGION_COLORS[blockType] ?? "#64748b"; // slate-500
}

export type ConfidenceLevel = "high" | "medium" | "low" | "unknown";

/** Thresholds are deliberately coarse — a teacher acts on "trust this" vs
 * "look closer", not on the third decimal place of a model's confidence. */
export function confidenceLevel(value: number | null | undefined): ConfidenceLevel {
  if (value === null || value === undefined) return "unknown";
  if (value >= 0.75) return "high";
  if (value >= 0.5) return "medium";
  return "low";
}

export const CONFIDENCE_STYLES: Record<ConfidenceLevel, string> = {
  high: "bg-green-100 text-green-700",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-red-100 text-red-700",
  unknown: "bg-slate-100 text-slate-500",
};

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined) return "unknown";
  return `${Math.round(value * 100)}%`;
}

export function regionLabel(region: RegionDetail): string {
  const question = region.question ? `${region.question} · ` : "";
  const page = region.page_number != null ? `p.${region.page_number}` : "no page";
  return `${question}${region.block_type} (${page})`;
}

/** Reading-order comparator — the same order the answer's regions arrive in
 * from GET /results/{answer_id}, and the order §7D merged their text in. */
export function byReadingOrder(a: RegionDetail, b: RegionDetail): number {
  return a.reading_order - b.reading_order;
}
