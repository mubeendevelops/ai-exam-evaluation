/**
 * The question paper preview's PDF drawing (P34, D224), over pdf.js. Loaded on demand: the
 * library is large and only the preview needs it. Tests replace this module.
 */

export interface PdfDocument {
  pages: number
  /** Draws page `number` (1-based) on the canvas at `scale` (1 = 72 dpi). `cancel` stops a
   * drawing still running (pdf.js refuses two at once on one canvas). */
  draw: (number: number, canvas: HTMLCanvasElement, scale: number) => Drawing
  close: () => void
}

export interface Drawing {
  done: Promise<void>
  cancel: () => void
}

export async function openPdf(data: ArrayBuffer): Promise<PdfDocument> {
  const pdfjs = await import('pdfjs-dist')
  const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default
  const doc = await pdfjs.getDocument({ data: new Uint8Array(data) }).promise
  return {
    pages: doc.numPages,
    draw(number, canvas, scale) {
      let cancelled = false
      let task: { cancel: () => void } | null = null
      const done = (async () => {
        const page = await doc.getPage(number)
        if (cancelled) return
        const ratio = globalThis.devicePixelRatio || 1
        const viewport = page.getViewport({ scale: scale * ratio })
        canvas.width = Math.floor(viewport.width)
        canvas.height = Math.floor(viewport.height)
        canvas.style.width = `${Math.floor(viewport.width / ratio)}px`
        canvas.style.height = `${Math.floor(viewport.height / ratio)}px`
        const rendering = page.render({ canvas, viewport })
        task = rendering
        await rendering.promise
      })()
      return {
        done,
        cancel() {
          cancelled = true
          task?.cancel()
        },
      }
    },
    close() {
      void doc.destroy()
    },
  }
}
