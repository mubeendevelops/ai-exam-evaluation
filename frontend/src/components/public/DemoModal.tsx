import { useState } from 'react'
import {
  aiEvaluationDemo,
  analyticsDemo,
  demoMeta,
  mapAnswersDemo,
  repositoryDemo,
} from '../../content/demos'
import type { DemoId } from '../../content/landing'
import { Badge, Modal } from '../ui'

function RepositoryDemo() {
  return (
    <div className="space-y-6">
      <p className="text-xs text-gray-300">{repositoryDemo.intro}</p>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-4 rounded-2xl border border-gray-800 bg-gray-950/80 p-5">
          <h3 className="flex items-center gap-2 text-sm font-bold text-purple-300">
            <i className="fa-solid fa-sitemap" aria-hidden="true" /> Question Schema Attributes
          </h3>
          <dl className="space-y-2 text-xs">
            {repositoryDemo.attributes.map((attr) => (
              <div
                key={attr.name}
                className="flex justify-between rounded-xl border border-gray-800 bg-gray-900/90 p-2.5"
              >
                <dt className="font-mono text-cyan-400">{attr.name}</dt>
                <dd className="text-gray-400">{attr.type}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="flex flex-col justify-between rounded-2xl border border-purple-500/30 bg-gray-950 p-5">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-mono text-[10px] text-purple-400">SCHEMA JSON RESPONSE</span>
            <span className="rounded bg-emerald-950 px-2 py-0.5 font-mono text-[10px] text-emerald-400">
              200 OK
            </span>
          </div>
          <pre className="custom-scrollbar overflow-x-auto rounded-xl bg-gray-900/80 p-3 font-mono text-[11px] leading-relaxed text-emerald-300">
            {repositoryDemo.json}
          </pre>
        </div>
      </div>
    </div>
  )
}

function MapAnswersDemo() {
  const [mode, setMode] = useState<'search' | 'upload'>('search')
  const on = 'rounded-lg bg-cyan-600 px-3 py-1.5 font-bold text-white transition'
  const off = 'rounded-lg px-3 py-1.5 text-gray-400 transition hover:text-white'
  return (
    <div className="space-y-6">
      <div className="flex flex-col items-start justify-between gap-4 rounded-2xl border border-cyan-500/30 bg-gray-950 p-4 sm:flex-row sm:items-center">
        <div>
          <h3 className="text-sm font-bold text-white">{mapAnswersDemo.heading}</h3>
          <p className="text-xs text-gray-400">{mapAnswersDemo.sub}</p>
        </div>
        <div
          role="group"
          aria-label="Key source"
          className="flex items-center gap-2 rounded-xl border border-gray-800 bg-gray-900 p-1 text-xs"
        >
          <button
            type="button"
            aria-pressed={mode === 'search'}
            onClick={() => setMode('search')}
            className={mode === 'search' ? on : off}
          >
            Web Search Link
          </button>
          <button
            type="button"
            aria-pressed={mode === 'upload'}
            onClick={() => setMode('upload')}
            className={mode === 'upload' ? on : off}
          >
            Direct Upload
          </button>
        </div>
      </div>

      <div className="space-y-4 rounded-2xl border border-gray-800 bg-gray-950/80 p-6">
        {mode === 'search' ? (
          <div className="space-y-3">
            <label className="block text-xs font-bold tracking-wider text-cyan-400 uppercase">
              {mapAnswersDemo.searchLabel}
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                readOnly
                value={mapAnswersDemo.searchValue}
                aria-label="Sample search"
                className="grow rounded-xl border border-gray-700 bg-gray-900 px-3 py-2 text-xs text-white focus:outline-none"
              />
              <button
                type="button"
                disabled
                title="Preview only"
                className="flex items-center gap-2 rounded-xl bg-cyan-600 px-4 py-2 text-xs font-bold text-white opacity-70"
              >
                <i className="fa-solid fa-magnifying-glass" aria-hidden="true" /> Crawl &amp; Map
              </button>
            </div>
            <div className="rounded-xl border border-cyan-500/20 bg-cyan-950/30 p-3 text-xs text-cyan-200">
              <i className="fa-solid fa-link mr-2 text-cyan-400" aria-hidden="true" /> Linked
              Reference: <strong>{mapAnswersDemo.linked}</strong> {mapAnswersDemo.linkedNote}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="block text-xs font-bold tracking-wider text-cyan-400 uppercase">
              {mapAnswersDemo.uploadLabel}
            </p>
            <div className="space-y-2 rounded-2xl border-2 border-dashed border-gray-700 p-6 text-center">
              <i className="fa-solid fa-file-arrow-up text-3xl text-cyan-400" aria-hidden="true" />
              <p className="text-xs font-semibold text-gray-300">{mapAnswersDemo.uploadHint}</p>
              <p className="text-[10px] text-gray-500">{mapAnswersDemo.uploadTypes}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function AiEvaluationDemo() {
  const demo = aiEvaluationDemo
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-400">
          Candidate: <strong className="text-white">{demo.candidate}</strong> ({demo.paper})
        </span>
        <Badge tone="purple" className="px-2.5 py-1 text-xs">
          Evaluation Mode Active
        </Badge>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="flex flex-col justify-between space-y-3 rounded-2xl border border-gray-800 bg-gray-950 p-4">
          <div className="flex items-center justify-between border-b border-gray-800 pb-2">
            <span className="flex items-center gap-1.5 text-xs font-bold text-purple-400">
              <i className="fa-solid fa-file-image" aria-hidden="true" /> 1. Segmented Answer
            </span>
            <span className="rounded bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300">
              Scan Region #2
            </span>
          </div>
          <div className="relative flex min-h-[180px] flex-col items-center justify-center overflow-hidden rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="border-b border-dashed border-gray-700 pb-2 text-center font-serif text-sm leading-relaxed text-gray-300 italic">
              {demo.scanLines.map((line, index) => (
                <span key={line}>
                  {index > 0 && <br />}
                  {line}
                </span>
              ))}
            </div>
            <span className="mt-2 font-mono text-[10px] text-purple-400">{demo.box}</span>
          </div>
          <p className="text-[10px] text-gray-400 italic">
            Auto-cropped from primary 300 DPI sheet scan.
          </p>
        </div>

        <div className="flex flex-col justify-between space-y-3 rounded-2xl border border-gray-800 bg-gray-950 p-4">
          <div className="flex items-center justify-between border-b border-gray-800 pb-2">
            <span className="flex items-center gap-1.5 text-xs font-bold text-cyan-400">
              <i className="fa-solid fa-font" aria-hidden="true" /> 2. Extracted OCR Text
            </span>
            <span className="rounded bg-cyan-950 px-2 py-0.5 text-[10px] text-cyan-300">
              {demo.confidence}
            </span>
          </div>
          <div className="min-h-[180px] space-y-2 rounded-xl border border-gray-800 bg-gray-900 p-3 font-mono text-xs text-cyan-200">
            {demo.ocrSteps.map((step, index) => (
              <p key={step} className="text-[11px]">
                <span className="text-gray-500">Step {index + 1}:</span> {step}
              </p>
            ))}
          </div>
          <p className="text-[10px] text-gray-400 italic">Normalized using LaTeX math parser.</p>
        </div>

        <div className="flex flex-col justify-between space-y-3 rounded-2xl border border-purple-500/40 bg-gray-950 p-4">
          <div className="flex items-center justify-between border-b border-gray-800 pb-2">
            <span className="flex items-center gap-1.5 text-xs font-bold text-emerald-400">
              <i className="fa-solid fa-user-check" aria-hidden="true" /> 3. AI Evaluation &amp;
              Override
            </span>
            <span className="rounded bg-emerald-950 px-2 py-0.5 text-[10px] text-emerald-300">
              {demo.suggested}
            </span>
          </div>
          <div className="min-h-[180px] space-y-3 rounded-xl border border-gray-800 bg-gray-900 p-3 text-xs">
            <div>
              <span className="text-[10px] font-bold text-gray-400 uppercase">AI Breakdown:</span>
              {demo.breakdown.map((line) => (
                <p key={line} className="text-[11px] text-emerald-400">
                  ✔ {line}
                </p>
              ))}
            </div>
            <div>
              <label
                htmlFor="demo-override"
                className="mb-1 block text-[10px] font-bold text-gray-400 uppercase"
              >
                Teacher Score Override
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="demo-override"
                  type="number"
                  readOnly
                  value={demo.maxMarks}
                  className="w-16 rounded-lg border border-gray-700 bg-gray-950 px-2 py-1 text-center text-xs font-bold text-white"
                />
                <span className="text-xs text-gray-400">/ {demo.maxMarks} Marks</span>
              </div>
            </div>
          </div>
          <button
            type="button"
            disabled
            title="Preview only"
            className="w-full rounded-xl bg-emerald-600 py-2 text-xs font-bold text-white opacity-70"
          >
            Confirm &amp; Commit Score
          </button>
        </div>
      </div>
    </div>
  )
}

const tileTone = {
  purple: ['border-purple-500/30', 'text-purple-400', 'text-purple-300'],
  cyan: ['border-cyan-500/30', 'text-cyan-400', 'text-cyan-300'],
  indigo: ['border-indigo-500/30', 'text-indigo-400', 'text-indigo-300'],
  emerald: ['border-emerald-500/30', 'text-emerald-400', 'text-emerald-300'],
} as const

function AnalyticsDemo() {
  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-950/30 p-3 text-xs text-amber-200">
        <i className="fa-solid fa-circle-info mt-0.5 text-amber-400" aria-hidden="true" />
        <p>
          <strong>Preview only.</strong> Analytics is not part of the product yet. The figures below
          are sample values for illustration.
        </p>
      </div>
      <p className="text-xs text-gray-300">{analyticsDemo.intro}</p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {analyticsDemo.tiles.map((tile) => {
          const [border, eyebrow, detail] = tileTone[tile.tone as keyof typeof tileTone]
          return (
            <div
              key={tile.tier}
              className={`space-y-2 rounded-2xl border bg-gray-950 p-4 ${border}`}
            >
              <span className={`text-[10px] font-bold tracking-widest uppercase ${eyebrow}`}>
                {tile.tier}
              </span>
              <div className="text-2xl font-black text-white">{tile.value}</div>
              <p className="text-xs text-gray-400">{tile.caption}</p>
              <div className={`border-t border-gray-800 pt-2 text-[11px] ${detail}`}>
                {tile.detail.map((line, index) => (
                  <span key={line}>
                    {index > 0 && <br />}
                    {line}
                  </span>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <div className="space-y-3 rounded-2xl border border-gray-800 bg-gray-950 p-5">
        <h4 className="text-xs font-bold tracking-wider text-white uppercase">
          {analyticsDemo.curveTitle}
        </h4>
        <div
          role="img"
          aria-label="Sample bell curve"
          className="flex h-28 items-end justify-between gap-2 border-b border-gray-800 px-4 pt-4"
        >
          {analyticsDemo.curve.map((bar, index) => (
            <div
              key={index}
              className={`w-full rounded-t ${bar.cls}`}
              style={{ height: `${bar.height}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

const bodies: Record<DemoId, () => React.JSX.Element> = {
  repository: RepositoryDemo,
  mapAnswers: MapAnswersDemo,
  aiEvaluation: AiEvaluationDemo,
  analytics: AnalyticsDemo,
}

/** The preview behind a feature card. Static: sample data, no calls to the API. */
export function DemoModal({ demo, onClose }: { demo: DemoId | null; onClose: () => void }) {
  const meta = demo ? demoMeta[demo] : null
  const Body = demo ? bodies[demo] : null
  return (
    <Modal
      open={demo !== null}
      onClose={onClose}
      variant="public"
      size="max-w-5xl"
      eyebrow={meta?.category}
      title={meta?.title ?? ''}
      footer={
        <div className="flex items-center justify-between text-xs text-gray-400">
          <span className="flex items-center gap-1.5">
            <i className="fa-solid fa-circle-check text-emerald-400" aria-hidden="true" /> Static
            preview with sample data
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-gray-800 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-gray-700"
          >
            Close Preview
          </button>
        </div>
      }
    >
      {Body && <Body />}
    </Modal>
  )
}
