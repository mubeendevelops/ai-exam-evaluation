// src/features/results/detail/useZoomPan.ts — zoom/pan state for the page
// image panel, in NATURAL PIXEL COORDINATES.
//
// `scale` maps natural pixels to viewport pixels; `tx`/`ty` are the viewport
// offset of the natural-space origin (0,0). A point at natural (x, y) draws
// at viewport (tx + x*scale, ty + y*scale). Keeping the transform in these
// terms — rather than a scale MULTIPLIER on top of "fit" — is what lets
// zoomToRegion and the wheel handler share one bit of math: pick a target
// scale, then solve tx/ty so a chosen natural point lands at a chosen
// viewport point.
//
// region_bbox on every RegionDetail (api/schemas/evaluation.py) is in pixel
// coordinates of the DESKEWED page image this panel renders — so an overlay
// drawn in the same natural-pixel space the image is, with no separate
// transform, lands exactly on the region at any zoom level.
import { useCallback, useEffect, useRef, useState } from "react";

export interface ImageSize {
  width: number;
  height: number;
}

const MIN_ZOOM_MULTIPLE = 1; // 1x the fit-to-viewport scale
const MAX_ZOOM_MULTIPLE = 8;

export function useZoomPan(viewportRef: React.RefObject<HTMLDivElement | null>, natural: ImageSize | null) {
  const [transform, setTransform] = useState({ scale: 1, tx: 0, ty: 0 });
  const [baseScale, setBaseScale] = useState(1);
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; startTx: number; startTy: number } | null>(
    null,
  );
  const pinchRef = useRef<{ distance: number; scale: number } | null>(null);

  const fit = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport || !natural) return;
    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;
    const scale = Math.min(vw / natural.width, vh / natural.height) || 1;
    setBaseScale(scale);
    setTransform({
      scale,
      tx: (vw - natural.width * scale) / 2,
      ty: (vh - natural.height * scale) / 2,
    });
  }, [natural, viewportRef]);

  // Re-fit whenever the image (or its page) changes, and on resize.
  useEffect(() => {
    fit();
    const viewport = viewportRef.current;
    if (!viewport) return;
    const observer = new ResizeObserver(() => fit());
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [fit, viewportRef]);

  const clampScale = useCallback(
    (scale: number) => Math.min(Math.max(scale, baseScale * MIN_ZOOM_MULTIPLE), baseScale * MAX_ZOOM_MULTIPLE),
    [baseScale],
  );

  /** Re-scales around a fixed VIEWPORT point (e.g. the cursor, or the
   * viewport center for the +/- buttons). */
  const zoomAt = useCallback(
    (viewportX: number, viewportY: number, nextScale: number) => {
      setTransform((prev) => {
        const clamped = clampScale(nextScale);
        const naturalX = (viewportX - prev.tx) / prev.scale;
        const naturalY = (viewportY - prev.ty) / prev.scale;
        return {
          scale: clamped,
          tx: viewportX - naturalX * clamped,
          ty: viewportY - naturalY * clamped,
        };
      });
    },
    [clampScale],
  );

  const zoomToRegion = useCallback(
    (bbox: [number, number, number, number]) => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      const [x, y, w, h] = bbox;
      const vw = viewport.clientWidth;
      const vh = viewport.clientHeight;
      // Fill ~55% of the viewport with the region, so its surroundings stay
      // visible for context.
      const padding = 1 / 0.55;
      const targetScale = clampScale(Math.min(vw / (w * padding), vh / (h * padding)));
      const centerX = x + w / 2;
      const centerY = y + h / 2;
      setTransform({
        scale: targetScale,
        tx: vw / 2 - centerX * targetScale,
        ty: vh / 2 - centerY * targetScale,
      });
    },
    [clampScale, viewportRef],
  );

  function onWheel(e: React.WheelEvent<HTMLDivElement>) {
    e.preventDefault();
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) return;
    const factor = Math.exp(-e.deltaY * 0.0015);
    setTransform((prev) => {
      const clamped = clampScale(prev.scale * factor);
      const viewportX = e.clientX - rect.left;
      const viewportY = e.clientY - rect.top;
      const naturalX = (viewportX - prev.tx) / prev.scale;
      const naturalY = (viewportY - prev.ty) / prev.scale;
      return { scale: clamped, tx: viewportX - naturalX * clamped, ty: viewportY - naturalY * clamped };
    });
  }

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.pointerType === "touch") return; // handled by touch pinch/pan below
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = { pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, startTx: transform.tx, startTy: transform.ty };
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    setTransform((prev) => ({
      ...prev,
      tx: drag.startTx + (e.clientX - drag.startX),
      ty: drag.startTy + (e.clientY - drag.startY),
    }));
  }

  function onPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId === e.pointerId) dragRef.current = null;
  }

  // Best-effort two-finger pinch-to-zoom. Pointer Events split each finger
  // into its own stream, so a simple drag handler can't see a second finger —
  // this listens for native touch events on the viewport instead, only while
  // exactly two fingers are down.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    function distance(touches: TouchList): number {
      const [a, b] = [touches[0], touches[1]];
      return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    }
    function midpoint(touches: TouchList): { x: number; y: number } {
      const [a, b] = [touches[0], touches[1]];
      const rect = viewport!.getBoundingClientRect();
      return { x: (a.clientX + b.clientX) / 2 - rect.left, y: (a.clientY + b.clientY) / 2 - rect.top };
    }

    function handleTouchMove(e: TouchEvent) {
      if (e.touches.length !== 2) return;
      e.preventDefault();
      const dist = distance(e.touches);
      const mid = midpoint(e.touches);
      if (!pinchRef.current) {
        pinchRef.current = { distance: dist, scale: transform.scale };
        return;
      }
      const nextScale = pinchRef.current.scale * (dist / pinchRef.current.distance);
      zoomAt(mid.x, mid.y, nextScale);
    }
    function handleTouchEnd(e: TouchEvent) {
      if (e.touches.length < 2) pinchRef.current = null;
    }

    viewport.addEventListener("touchmove", handleTouchMove, { passive: false });
    viewport.addEventListener("touchend", handleTouchEnd);
    viewport.addEventListener("touchcancel", handleTouchEnd);
    return () => {
      viewport.removeEventListener("touchmove", handleTouchMove);
      viewport.removeEventListener("touchend", handleTouchEnd);
      viewport.removeEventListener("touchcancel", handleTouchEnd);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewportRef, zoomAt, transform.scale]);

  function zoomIn() {
    const viewport = viewportRef.current;
    if (!viewport) return;
    zoomAt(viewport.clientWidth / 2, viewport.clientHeight / 2, transform.scale * 1.4);
  }
  function zoomOut() {
    const viewport = viewportRef.current;
    if (!viewport) return;
    zoomAt(viewport.clientWidth / 2, viewport.clientHeight / 2, transform.scale / 1.4);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const step = 40;
    switch (e.key) {
      case "+":
      case "=":
        e.preventDefault();
        zoomIn();
        break;
      case "-":
      case "_":
        e.preventDefault();
        zoomOut();
        break;
      case "0":
        e.preventDefault();
        fit();
        break;
      case "ArrowUp":
        e.preventDefault();
        setTransform((p) => ({ ...p, ty: p.ty + step }));
        break;
      case "ArrowDown":
        e.preventDefault();
        setTransform((p) => ({ ...p, ty: p.ty - step }));
        break;
      case "ArrowLeft":
        e.preventDefault();
        setTransform((p) => ({ ...p, tx: p.tx + step }));
        break;
      case "ArrowRight":
        e.preventDefault();
        setTransform((p) => ({ ...p, tx: p.tx - step }));
        break;
    }
  }

  return {
    transform,
    isZoomed: transform.scale > baseScale + 0.001,
    zoomIn,
    zoomOut,
    resetToFit: fit,
    zoomToRegion,
    handlers: { onWheel, onPointerDown, onPointerMove, onPointerUp, onKeyDown },
  };
}
