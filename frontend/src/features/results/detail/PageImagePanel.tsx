// src/features/results/detail/PageImagePanel.tsx — LEFT PANEL: the scanned
// page, zoomed/panned, with region overlays.
//
// The image URL is NEVER trusted as a persisted value (CLAUDE_CONTEXT.md
// §11): GET /results/{id}/pages/{n}/image mints a fresh 300s presigned URL
// per request, so this panel re-fetches it per page rather than caching a
// URL across the session.
import { useEffect, useRef, useState } from "react";

import { usePageImage, type PageSummary, type RegionDetail } from "../../../api/queries";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { Loading } from "../../../components/Loading";
import { useSelection } from "./SelectionContext";
import { confidenceLevel, regionColor, regionLabel } from "./helpers";
import { useZoomPan } from "./useZoomPan";

interface PageImagePanelProps {
  answerId: string;
  pages: PageSummary[];
  regionsByPage: Map<number, RegionDetail[]>;
}

export function PageImagePanel({ answerId, pages, regionsByPage }: PageImagePanelProps) {
  const [pageNumber, setPageNumber] = useState<number | undefined>(pages[0]?.page_number);
  const [overlaysOn, setOverlaysOn] = useState(true);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const { selectedBlockId, select, zoomToken } = useSelection();

  const image = usePageImage(answerId, pageNumber);
  const zoomPan = useZoomPan(viewportRef, naturalSize);
  const regionsOnPage = pageNumber !== undefined ? (regionsByPage.get(pageNumber) ?? []) : [];

  // Reset the loaded-image size when the page changes so useZoomPan doesn't
  // fit against the PREVIOUS page's dimensions for one frame.
  useEffect(() => setNaturalSize(null), [pageNumber]);

  // Selecting a region on another page should switch to that page — the
  // overlay for a region only exists once its page is on screen.
  useEffect(() => {
    if (!selectedBlockId) return;
    for (const [page, regions] of regionsByPage) {
      if (regions.some((r) => r.block_id === selectedBlockId) && page !== pageNumber) {
        setPageNumber(page);
        return;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBlockId]);

  // Zoom to the selected region once its page's image has loaded.
  useEffect(() => {
    if (!selectedBlockId || !naturalSize) return;
    const region = regionsOnPage.find((r) => r.block_id === selectedBlockId);
    if (region?.region_bbox && region.region_bbox.length === 4) {
      zoomPan.zoomToRegion(region.region_bbox as [number, number, number, number]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBlockId, naturalSize, zoomToken]);

  if (pages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-400">
        No scanned pages are available for this answer.
      </div>
    );
  }

  const currentPageSummary = pages.find((p) => p.page_number === pageNumber);

  return (
    <div className="flex h-full flex-col rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
        <div className="flex items-center gap-1">
          {pages.map((page) => (
            <button
              key={page.page_number}
              type="button"
              onClick={() => setPageNumber(page.page_number)}
              aria-current={page.page_number === pageNumber ? "page" : undefined}
              className={[
                "relative rounded px-2.5 py-1 text-xs font-medium",
                page.page_number === pageNumber ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200",
              ].join(" ")}
            >
              Page {page.page_number}
              {page.needs_review && (
                <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-red-500" aria-label="Needs review" />
              )}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-slate-600">
            <input type="checkbox" checked={overlaysOn} onChange={(e) => setOverlaysOn(e.target.checked)} />
            Show regions
          </label>
          <div className="h-4 w-px bg-slate-200" />
          <button type="button" className="rounded px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100" onClick={zoomPan.zoomOut} aria-label="Zoom out">
            −
          </button>
          <button type="button" className="rounded px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100" onClick={zoomPan.zoomIn} aria-label="Zoom in">
            +
          </button>
          <button type="button" className="rounded px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100" onClick={zoomPan.resetToFit}>
            Reset
          </button>
        </div>
      </div>

      <div
        ref={viewportRef}
        role="application"
        aria-label="Scanned page. Use arrow keys to pan, plus and minus to zoom, 0 to reset."
        tabIndex={0}
        className="relative flex-1 touch-none overflow-hidden bg-slate-900/5 outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        style={{ minHeight: 420, cursor: zoomPan.isZoomed ? "grab" : "default" }}
        onWheel={zoomPan.handlers.onWheel}
        onPointerDown={zoomPan.handlers.onPointerDown}
        onPointerMove={zoomPan.handlers.onPointerMove}
        onPointerUp={zoomPan.handlers.onPointerUp}
        onKeyDown={zoomPan.handlers.onKeyDown}
      >
        {image.isLoading && <Loading label="Loading page image…" />}
        {image.error && (
          <div className="p-4">
            <ErrorMessage message={(image.error as Error).message} onRetry={() => void image.refetch()} />
          </div>
        )}
        {image.data && (
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              transform: `translate(${zoomPan.transform.tx}px, ${zoomPan.transform.ty}px) scale(${zoomPan.transform.scale})`,
              transformOrigin: "0 0",
            }}
          >
            {/* eslint-disable-next-line jsx-a11y/alt-text -- alt below */}
            <img
              src={image.data.url}
              alt={`Scanned page ${pageNumber}`}
              draggable={false}
              onLoad={(e) => setNaturalSize({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })}
            />
            {overlaysOn && naturalSize && (
              <svg
                width={naturalSize.width}
                height={naturalSize.height}
                viewBox={`0 0 ${naturalSize.width} ${naturalSize.height}`}
                className="absolute left-0 top-0"
              >
                {regionsOnPage.map((region) => (
                  <RegionOverlay
                    key={region.block_id}
                    region={region}
                    isSelected={region.block_id === selectedBlockId}
                    onSelect={() => select(region.block_id, { zoom: true })}
                  />
                ))}
              </svg>
            )}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-slate-200 px-3 py-1.5 text-xs text-slate-400">
        <Legend />
        <span>
          Page {pageNumber} of {pages[pages.length - 1]?.page_number ?? pages.length}
          {currentPageSummary && currentPageSummary.regions > 0 ? ` · ${currentPageSummary.regions} region(s)` : ""}
        </span>
      </div>
    </div>
  );
}

function RegionOverlay({ region, isSelected, onSelect }: { region: RegionDetail; isSelected: boolean; onSelect: () => void }) {
  if (!region.region_bbox || region.region_bbox.length !== 4) return null;
  const [x, y, w, h] = region.region_bbox;
  const color = regionColor(region.block_type);
  const level = confidenceLevel(region.classification_confidence);

  return (
    <g
      role="button"
      tabIndex={-1}
      aria-label={regionLabel(region)}
      onClick={onSelect}
      style={{ cursor: "pointer" }}
    >
      {/* White halo first, for contrast over dark ink, then the coloured
          stroke on top — see helpers.ts's regionColor docstring. */}
      <rect x={x} y={y} width={w} height={h} fill="none" stroke="white" strokeWidth={isSelected ? 7 : 5} opacity={0.85} />
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        fill={color}
        fillOpacity={isSelected ? 0.18 : 0.06}
        stroke={color}
        strokeWidth={isSelected ? 3.5 : 2}
        strokeDasharray={level === "low" ? "6 4" : undefined}
      />
      {region.needs_review && (
        <circle cx={x + w - 6} cy={y + 6} r={7} fill="#dc2626" stroke="white" strokeWidth={1.5} />
      )}
    </g>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-3">
      <LegendSwatch color={regionColor("text")} label="Text" />
      <LegendSwatch color={regionColor("table")} label="Table" />
      <LegendSwatch color={regionColor("diagram")} label="Diagram" />
    </div>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}
