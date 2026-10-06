import { isTextLine, type LineView, type SegmentView } from '../../lib/segments'
import { useFileUrl } from '../qna/AuthImage'
import { GlassPanel } from '../ui'
import type { BookletDetail } from './model'

const pct = (n: number, whole: number) => `${(n / whole) * 100}%`

/**
 * "Scanned Answer Sheet": the cleaned page with a box around every answer found on it. Boxes
 * are the segments' areas (green: an answer to a question, amber: unassigned, pink: a question
 * answered twice); dashed amber outlines mark lines the OCR was unsure of. While the machine is
 * still working on the booklet, the prototype's scanner beam sweeps the page.
 */
export function PagePanel({
  booklet,
  views,
  selected,
  onSelect,
  editingLine,
  scanning,
  status,
  pageNumber,
  onPage,
}: {
  booklet: BookletDetail
  views: SegmentView[]
  selected: string | null
  onSelect: (segmentId: string) => void
  editingLine: LineView | null
  scanning: boolean
  /** One line saying what the machine is doing. */
  status: string | null
  pageNumber: number
  onPage: (number: number) => void
}) {
  const pages = booklet.pages.filter((p) => p.cleaned)
  const page = pages.find((p) => p.number === pageNumber) ?? pages[0]
  const file = useFileUrl(page?.image_url)

  const here = page ? views.filter((v) => v.boxes.has(page.number)) : []
  const unsure = page
    ? views
        .flatMap((v) => v.lines)
        .filter((l) => l.page === page.number && l.flagged && !l.struck && isTextLine(l))
    : []

  return (
    <GlassPanel className="flex flex-col p-4 lg:col-span-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
          <i className="fa-solid fa-file-contract text-cyan-400" aria-hidden="true" />
          Scanned Answer Sheet: <span className="text-cyan-300">{booklet.student.name}</span>
        </h3>
        {pages.length > 1 && (
          <div className="flex items-center gap-1 text-xs text-gray-300">
            <button
              type="button"
              aria-label="Previous page"
              disabled={!page || page.number === pages[0]?.number}
              onClick={() => page && onPage(Math.max(1, page.number - 1))}
              className="rounded-md border border-gray-700 bg-gray-800 px-2 py-1 disabled:opacity-40"
            >
              <i className="fa-solid fa-chevron-left" aria-hidden="true" />
            </button>
            <span aria-live="polite">
              Page {page?.number} of {booklet.page_count}
            </span>
            <button
              type="button"
              aria-label="Next page"
              disabled={!page || page.number === pages[pages.length - 1]?.number}
              onClick={() => page && onPage(page.number + 1)}
              className="rounded-md border border-gray-700 bg-gray-800 px-2 py-1 disabled:opacity-40"
            >
              <i className="fa-solid fa-chevron-right" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>

      <div className="relative flex min-h-[440px] grow items-center justify-center overflow-hidden rounded-xl border border-gray-800 bg-gray-950 p-2">
        {!page && (
          <p className="text-xs text-gray-400">
            {status ?? 'The cleaned pages appear here as soon as they are ready.'}
          </p>
        )}
        {page && (
          <div className="relative w-full max-w-md select-none">
            {file.data ? (
              <img
                src={file.data}
                alt={`Page ${page.number} of ${booklet.student.name}'s booklet`}
                className="block w-full rounded bg-stone-100 shadow-2xl"
                width={page.width}
                height={page.height}
              />
            ) : (
              <div
                className="flex w-full items-center justify-center rounded bg-stone-100 text-xs text-gray-500"
                style={{ aspectRatio: `${page.width} / ${page.height}` }}
              >
                {file.isError ? 'The page image is unavailable.' : 'Loading the page…'}
              </div>
            )}
            {unsure.map((l) => (
              <div
                key={l.id}
                data-testid="unsure-line"
                aria-hidden="true"
                className="pointer-events-none absolute border border-dashed border-amber-500 bg-amber-400/20"
                style={{
                  left: pct(l.box[0], page.width),
                  top: pct(l.box[1], page.height),
                  width: pct(l.box[2] - l.box[0], page.width),
                  height: pct(l.box[3] - l.box[1], page.height),
                }}
              />
            ))}
            {here.map((v) => {
              const box = v.boxes.get(page.number)
              if (!box) return null
              const dup = v.segment.flags.includes('duplicate')
              const tone =
                v.label === null
                  ? 'border-amber-500 bg-amber-500/10'
                  : dup
                    ? 'border-pink-500 bg-pink-500/10'
                    : 'border-emerald-500 bg-emerald-500/10'
              const tag = v.label === null ? 'bg-amber-600' : dup ? 'bg-pink-600' : 'bg-emerald-600'
              const on = selected === v.segment.id
              return (
                <button
                  key={v.segment.id}
                  type="button"
                  aria-label={
                    v.label === null
                      ? `Unassigned text on page ${page.number}`
                      : `Answer ${v.label} on page ${page.number}`
                  }
                  aria-pressed={on}
                  onClick={() => onSelect(v.segment.id)}
                  className={`absolute rounded border-2 ${tone} ${on ? 'ring-2 ring-cyan-300' : ''}`}
                  style={{
                    left: pct(box[0], page.width),
                    top: pct(box[1], page.height),
                    width: pct(box[2] - box[0], page.width),
                    height: pct(box[3] - box[1], page.height),
                  }}
                >
                  <span
                    className={`absolute -top-2.5 left-2 rounded px-1.5 py-0.5 font-sans text-[10px] font-bold text-white uppercase ${tag}`}
                  >
                    {v.label === null ? 'Unassigned' : `Segmented Q${v.label}`}
                  </span>
                </button>
              )
            })}
            {editingLine && editingLine.page === page.number && (
              <div
                aria-hidden="true"
                className="pointer-events-none absolute border-2 border-cyan-300 bg-cyan-300/20"
                style={{
                  left: pct(editingLine.box[0], page.width),
                  top: pct(editingLine.box[1], page.height),
                  width: pct(editingLine.box[2] - editingLine.box[0], page.width),
                  height: pct(editingLine.box[3] - editingLine.box[1], page.height),
                }}
              />
            )}
          </div>
        )}
        {scanning && <div className="scan-line" aria-hidden="true" data-testid="scan-beam" />}
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-gray-400">
        <li>
          <span className="mr-1 inline-block h-2 w-3 rounded-sm border border-emerald-500 bg-emerald-500/20" />
          Answer to a question
        </li>
        <li>
          <span className="mr-1 inline-block h-2 w-3 rounded-sm border border-amber-500 bg-amber-500/20" />
          Unassigned
        </li>
        <li>
          <span className="mr-1 inline-block h-2 w-3 rounded-sm border border-dashed border-amber-500 bg-amber-400/20" />
          Line the OCR is unsure of
        </li>
      </ul>
      {status && page && (
        <p role="status" className="mt-2 text-xs text-cyan-300">
          {status}
        </p>
      )}
    </GlassPanel>
  )
}
