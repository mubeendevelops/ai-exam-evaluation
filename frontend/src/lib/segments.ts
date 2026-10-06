/** What the segmentation screen shows, built from the booklet's pages, lines and segments. */
import type { components } from '../api/schema'
import type { Rect } from './graph'

export type Segment = components['schemas']['SegmentOut']
export type PageText = components['schemas']['PageTextOut']
export type Region = components['schemas']['RegionOut']

export interface LineView {
  id: string
  page: number
  box: Rect
  kind: Region['kind']
  text: string
  /** Below the OCR line threshold: highlighted for the teacher. */
  flagged: boolean
  struck: boolean
  /** The selector's score for the line, 0..1. */
  score: number | null
  /** Engines that read it, for the tooltip. */
  readBy: string[]
}

export interface SegmentView {
  segment: Segment
  /** The question it answers; null: the unassigned tray. */
  label: string | null
  lines: LineView[]
  /** Mean OCR line score of its text lines, 0..1; null when none has one. */
  confidence: number | null
  lowLines: number
  pages: number[]
  /** The area it covers on each page (the union of its lines' boxes). */
  boxes: Map<number, Rect>
}

const TEXT_KINDS = new Set<Region['kind']>(['text_line', 'text_block', 'label'])

export function asRect(box: number[]): Rect {
  return [box[0] ?? 0, box[1] ?? 0, box[2] ?? 0, box[3] ?? 0]
}

export function union(a: Rect, b: Rect): Rect {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]
}

/** Every region of the booklet by id, with the page it is on. */
export function indexRegions(texts: Map<number, PageText>): Map<string, LineView> {
  const found = new Map<string, LineView>()
  for (const [page, text] of texts) {
    for (const r of text.regions) {
      found.set(r.id, {
        id: r.id,
        page,
        box: asRect(r.box),
        kind: r.kind,
        text: r.text ?? '',
        flagged: r.flagged,
        struck: r.struck_out,
        score: r.line_score,
        readBy: r.read_by,
      })
    }
  }
  return found
}

export function buildSegments(segments: Segment[], texts: Map<number, PageText>): SegmentView[] {
  const regions = indexRegions(texts)
  return [...segments]
    .sort((a, b) => a.position - b.position)
    .map((segment) => {
      const lines = segment.region_ids.flatMap((id) => {
        const line = regions.get(id)
        return line ? [line] : []
      })
      const boxes = new Map<number, Rect>()
      for (const line of lines) {
        const seen = boxes.get(line.page)
        boxes.set(line.page, seen ? union(seen, line.box) : line.box)
      }
      const scored = lines.filter((l) => TEXT_KINDS.has(l.kind) && !l.struck && l.score !== null)
      const confidence = scored.length
        ? scored.reduce((sum, l) => sum + (l.score ?? 0), 0) / scored.length
        : null
      return {
        segment,
        label: segment.slot_label,
        lines,
        confidence,
        lowLines: lines.filter((l) => l.flagged && !l.struck).length,
        pages: [...boxes.keys()].sort((a, b) => a - b),
        boxes,
      }
    })
}

export function isTextLine(line: LineView): boolean {
  return TEXT_KINDS.has(line.kind)
}

export function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`
}

/** The answer labels of a blueprint document: `"7"` for a question, `"12.a"` for a sub-part. */
export function leafLabels(document: Record<string, unknown>): string[] {
  const labels: string[] = []
  const sections = Array.isArray(document.sections) ? document.sections : []
  const addQuestion = (q: unknown) => {
    if (typeof q !== 'object' || q === null) return
    const { label, parts } = q as { label?: unknown; parts?: unknown }
    if (typeof label !== 'string') return
    if (Array.isArray(parts) && parts.length > 0) {
      for (const p of parts) {
        const part = (p as { label?: unknown }).label
        if (typeof part === 'string') labels.push(`${label}.${part}`)
      }
    } else {
      labels.push(label)
    }
  }
  for (const section of sections) {
    const items = (section as { items?: unknown }).items
    if (!Array.isArray(items)) continue
    for (const item of items) {
      if (typeof item !== 'object' || item === null) continue
      const alternatives = (item as { alternatives?: unknown }).alternatives
      if (Array.isArray(alternatives)) alternatives.forEach(addQuestion)
      else addQuestion(item)
    }
  }
  return labels
}
