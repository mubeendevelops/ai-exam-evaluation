import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { fetchBlob, saveBlob } from '../../api/files'
import { openPdf, type PdfDocument } from '../../lib/paperPdf'
import { SmallButton } from '../schema/controls'
import { Modal, useToast } from '../ui'
import { paperFileName, paperPdfUrl } from './papers'

export const ZOOM = { min: 50, max: 180, step: 10, reset: 100 }

/** A point on the PDF is drawn this many CSS pixels wide at 100 %. */
const BASE_SCALE = 1.25

interface Paper {
  id: string
  version: number
  title: string
  exam_code: string
}

/** The paper's PDF bytes for one version (D224), fetched with the bearer token. */
function usePaperPdf(paper: Paper) {
  return useQuery({
    queryKey: ['paper-pdf', paper.id, paper.version],
    queryFn: () => fetchBlob(paperPdfUrl(paper.id, paper.version)),
    retry: false,
    gcTime: 0,
  })
}

/** Saves a paper version's PDF; the toast says when it fails. */
export function useDownloadPaper() {
  const toast = useToast()
  const [busy, setBusy] = useState('')
  async function download(paper: Paper, blob?: Blob) {
    setBusy(paper.id)
    try {
      saveBlob(
        blob ?? (await fetchBlob(paperPdfUrl(paper.id, paper.version))),
        paperFileName(paper),
      )
    } catch {
      toast.show('The PDF could not be downloaded. Try again.', 'red')
    } finally {
      setBusy('')
    }
  }
  return { download, busy }
}

/**
 * Question paper preview (mock modal 5, P34): the PDF the server renders for this version,
 * drawn in the page with zoom out / in by 10 % between 50 % and 180 %, Reset to 100 %, and
 * Download (the same file).
 */
export function PaperPreview({ paper, onClose }: { paper: Paper; onClose: () => void }) {
  const pdf = usePaperPdf(paper)
  const [zoom, setZoom] = useState(ZOOM.reset)
  const [doc, setDoc] = useState<PdfDocument | null>(null)
  const [failed, setFailed] = useState(false)
  const { download, busy } = useDownloadPaper()

  useEffect(() => {
    if (!pdf.data) return
    let open: PdfDocument | null = null
    let cancelled = false
    void (async () => {
      try {
        const loaded = await openPdf(await pdf.data.arrayBuffer())
        if (cancelled) {
          loaded.close()
          return
        }
        open = loaded
        setDoc(loaded)
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
      open?.close()
    }
  }, [pdf.data])

  const problem = pdf.isError || failed

  return (
    <Modal
      open
      onClose={onClose}
      size="max-w-5xl"
      eyebrow="Question paper preview"
      title={`${paper.title} - Preview`}
      description={`Version ${paper.version}${paper.exam_code ? ` · ${paper.exam_code}` : ''}. Question text and marks only: no keys or rubrics.`}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2" role="group" aria-label="Zoom">
            <SmallButton
              tone="gray"
              aria-label="Zoom out"
              disabled={zoom <= ZOOM.min}
              onClick={() => setZoom((z) => Math.max(ZOOM.min, z - ZOOM.step))}
            >
              −
            </SmallButton>
            <span className="w-12 text-center font-mono text-xs text-gray-200" aria-live="polite">
              {zoom}%
            </span>
            <SmallButton
              tone="gray"
              aria-label="Zoom in"
              disabled={zoom >= ZOOM.max}
              onClick={() => setZoom((z) => Math.min(ZOOM.max, z + ZOOM.step))}
            >
              +
            </SmallButton>
            <SmallButton
              tone="gray"
              disabled={zoom === ZOOM.reset}
              onClick={() => setZoom(ZOOM.reset)}
            >
              Reset
            </SmallButton>
          </div>
          <div className="flex gap-2">
            <SmallButton
              icon="fa-solid fa-file-arrow-down"
              disabled={!pdf.data || busy !== ''}
              onClick={() => void download(paper, pdf.data)}
            >
              Download PDF
            </SmallButton>
            <SmallButton tone="gray" onClick={onClose}>
              Close
            </SmallButton>
          </div>
        </div>
      }
    >
      {problem && (
        <p role="alert" className="text-xs text-red-300">
          The paper could not be shown. Check that it is still in your subjects and try again.
        </p>
      )}
      {!problem && !doc && <p className="text-xs text-gray-400">Preparing the PDF…</p>}
      {doc && (
        <div
          className="max-h-[65vh] overflow-auto rounded-xl bg-gray-700/40 p-3"
          aria-label={`${paper.title}, ${doc.pages} ${doc.pages === 1 ? 'page' : 'pages'}`}
          role="document"
        >
          <div className="flex flex-col items-center gap-3">
            {Array.from({ length: doc.pages }, (_, i) => (
              <PdfPage key={i} doc={doc} number={i + 1} scale={(zoom / 100) * BASE_SCALE} />
            ))}
          </div>
        </div>
      )}
    </Modal>
  )
}

function PdfPage({ doc, number, scale }: { doc: PdfDocument; number: number; scale: number }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    if (!canvas.current) return
    const drawing = doc.draw(number, canvas.current, scale)
    drawing.done.catch(() => undefined) // a cancelled drawing rejects
    return () => drawing.cancel()
  }, [doc, number, scale])
  return (
    <canvas
      ref={canvas}
      className="bg-white shadow-lg"
      aria-label={`Page ${number} of ${doc.pages}`}
      role="img"
    />
  )
}
