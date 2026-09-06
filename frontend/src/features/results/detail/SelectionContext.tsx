// src/features/results/detail/SelectionContext.tsx — the ONE piece of shared
// state that makes the two panels feel like one screen.
//
// Selecting a region is a single fact (`selectedBlockId`) that both panels
// read and both panels can set — clicking an overlay on the image and
// clicking a region card in the text panel are the same action from this
// state's point of view. `requestZoomToRegion`/`requestScrollToRegion` are
// separate from selection itself: selecting a region should always highlight
// it on both sides, but a caller sometimes wants to select WITHOUT forcing a
// zoom (e.g. arrowing through regions that are already in view).
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import type { RegionDetail } from "../../../api/queries";
import { byReadingOrder } from "./helpers";

interface SelectionContextValue {
  selectedBlockId: string | null;
  select: (blockId: string | null, options?: { zoom?: boolean }) => void;
  /** Bumped every time `select` is called with zoom:true, so the image panel
   * can react even when the same region is re-selected. */
  zoomToken: number;
  selectNext: (direction: 1 | -1) => void;
  regions: RegionDetail[];
}

const SelectionContext = createContext<SelectionContextValue | undefined>(undefined);

export function SelectionProvider({ regions, children }: { regions: RegionDetail[]; children: ReactNode }) {
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
  const [zoomToken, setZoomToken] = useState(0);
  const sorted = useMemo(() => [...regions].sort(byReadingOrder), [regions]);

  function select(blockId: string | null, options?: { zoom?: boolean }) {
    setSelectedBlockId(blockId);
    if (options?.zoom) setZoomToken((t) => t + 1);
  }

  function selectNext(direction: 1 | -1) {
    const list = sorted;
    if (list.length === 0) return;
    const currentIndex = list.findIndex((r) => r.block_id === selectedBlockId);
    const nextIndex = currentIndex === -1 ? 0 : (currentIndex + direction + list.length) % list.length;
    select(list[nextIndex].block_id, { zoom: true });
  }

  const value: SelectionContextValue = { selectedBlockId, select, zoomToken, selectNext, regions: sorted };

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function useSelection(): SelectionContextValue {
  const ctx = useContext(SelectionContext);
  if (!ctx) throw new Error("useSelection() must be used inside <SelectionProvider>.");
  return ctx;
}
