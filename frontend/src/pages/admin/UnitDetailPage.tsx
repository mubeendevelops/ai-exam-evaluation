import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { api } from '../../api/client'
import { uploadToUnit } from '../../api/booklets'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError } from '../../components/public/fields'
import { Dropzone } from '../../components/evaluate/Dropzone'
import { STATUS } from '../../components/evaluate/model'
import { StatusBadge } from '../../components/evaluate/StatusBadge'
import { EmptyState, GlassPanel, useToast } from '../../components/ui'
import { RowButton, sectionTitle } from './controls'
import type { UploadedFile, WorkUnitDetail } from './data'
import { RetakePanel } from './RetakePanel'

const isPdf = (f: File) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name)

function Results({ files, onDismiss }: { files: UploadedFile[]; onDismiss: () => void }) {
  const unknown = files.some((f) => f.problem === 'unknown_usn')
  return (
    <section aria-label="Upload results" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold tracking-wider text-gray-400 uppercase">
          Last upload
        </h3>
        <RowButton onClick={onDismiss}>Dismiss</RowButton>
      </div>
      <ul className="space-y-1 text-xs">
        {files.map((f, n) => (
          <li
            key={`${n}-${f.name}`}
            className="flex flex-wrap items-baseline gap-2 rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2"
          >
            <span className="font-mono text-gray-200">{f.name || '(no name)'}</span>
            {f.outcome === 'queued' ? (
              <span className="text-emerald-300">Queued</span>
            ) : (
              <span className={f.outcome === 'not_queued' ? 'text-amber-300' : 'text-red-300'}>
                {f.message}
              </span>
            )}
          </li>
        ))}
      </ul>
      {unknown && (
        <p className="text-xs text-gray-300">
          Roll numbers the roster does not know are added or corrected in{' '}
          <Link to="/admin/upload/students" className="font-semibold text-purple-300 underline">
            Students
          </Link>
          ; then upload those files again.
        </p>
      )}
    </section>
  )
}

function Booklets({ unit }: { unit: WorkUnitDetail }) {
  const [open, setOpen] = useState<string | null>(null)
  if (unit.booklets.length === 0) {
    return (
      <EmptyState icon="fa-solid fa-file-circle-plus" title="No booklets yet">
        Drop the students' PDFs above, each named by the roll number.
      </EmptyState>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Booklets of the unit</caption>
        <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
          <tr>
            <th scope="col" className="pr-4 pb-2">
              USN
            </th>
            <th scope="col" className="pr-4 pb-2">
              Student
            </th>
            <th scope="col" className="pr-4 pb-2">
              Pages
            </th>
            <th scope="col" className="pr-4 pb-2">
              Status
            </th>
            <th scope="col" className="pb-2 text-right">
              Retake
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800">
          {unit.booklets.map((b) => {
            const retake = b.status === 'needs_retake' || b.status === 'failed'
            return (
              <tr key={b.id} className="align-top">
                <td className="py-2 pr-4 font-mono text-cyan-300">{b.student.usn}</td>
                <th scope="row" className="py-2 pr-4 text-left font-semibold text-white">
                  {b.student.name}
                </th>
                <td className="py-2 pr-4 text-gray-300">{b.page_count || '—'}</td>
                <td className="py-2 pr-4">
                  <StatusBadge status={b.status} />
                  {open === b.id && (
                    <div className="mt-2">
                      <RetakePanel booklet={b} unitId={unit.id} />
                    </div>
                  )}
                </td>
                <td className="py-2 text-right">
                  {retake && (
                    <RowButton onClick={() => setOpen(open === b.id ? null : b.id)}>
                      {open === b.id ? 'Hide' : `Retake ${b.student.usn}`}
                    </RowButton>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * `/admin/upload/units/:unitId` (P26): the unit, a drop zone for its booklets (several PDFs,
 * one per student, named by the roll number), what happened to each file, and the retakes.
 * Statuses only: the admin sees no marks, text or pages here (D167).
 */
export default function UnitDetailPage() {
  const { unitId = '' } = useParams()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [sending, setSending] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [results, setResults] = useState<UploadedFile[] | null>(null)
  const unit = useQuery({
    queryKey: ['unit', unitId],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/units/{unit_id}', {
        params: { path: { unit_id: unitId } },
      })
      if (!data) throw new Error('unit unavailable')
      return data
    },
    refetchInterval: (query) =>
      query.state.data?.booklets.some((b) => STATUS[b.status].working) ? 4000 : false,
  })

  async function send(files: File[]) {
    setProblem(null)
    const u = unit.data
    if (!u) return
    if (!files.every(isPdf)) {
      setProblem('Only PDFs: one per student, named by the roll number.')
      return
    }
    if (files.length > u.max_files) {
      setProblem(`At most ${u.max_files} PDFs per upload.`)
      return
    }
    setSending(true)
    const result = await uploadToUnit(u.id, files)
    setSending(false)
    if (result.ok) {
      setResults(result.value.files)
      toast.show(
        `${result.value.queued} queued, ${result.value.refused} refused, ${result.value.not_queued} not queued.`,
      )
      await queryClient.invalidateQueries({ queryKey: ['unit', unitId] })
      await queryClient.invalidateQueries({ queryKey: ['units'] })
    } else setProblem(result.message)
  }

  const u = unit.data
  const free = u ? u.max_waiting - u.waiting : 0
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-layer-group"
        iconTone="text-cyan-400"
        title={u ? `Unit ${u.code}` : 'Unit'}
        description={
          u
            ? `${u.paper.exam_code} · ${u.paper.title} · ${u.subject.name} · ${u.year} batch · ${u.owner.name} · due ${u.due_date}`
            : 'Loading the unit…'
        }
      >
        <Link
          to="/admin/upload"
          className="inline-flex items-center gap-2 text-xs font-semibold text-purple-300 hover:text-white"
        >
          <i className="fa-solid fa-arrow-left" aria-hidden="true" /> Upload & Assign Papers
        </Link>
      </PageBanner>
      {unit.isError && <FormError message="Could not load the unit." />}
      {u && (
        <>
          <GlassPanel as="section" aria-labelledby="upload-title" className="space-y-4 p-6">
            <h2 id="upload-title" className={sectionTitle}>
              <i className="fa-solid fa-cloud-arrow-up text-purple-400" aria-hidden="true" />
              Upload booklets
            </h2>
            <Dropzone
              disabled={sending || free <= 0}
              hint={
                sending
                  ? 'Uploading…'
                  : free <= 0
                    ? `${u.waiting} booklets are waiting to be processed. Upload more once they are.`
                    : `${free} free queue ${free === 1 ? 'slot' : 'slots'}; files beyond them are not queued.`
              }
              onFiles={(files) => void send(files)}
              accept=".pdf,application/pdf"
              title="Drop the students' PDFs"
              subtitle="One PDF per student, named by the roll number, for example 1XX21CS001.pdf."
              label="Drop the students' PDFs, or press to choose them"
            />
            {problem && (
              <p role="alert" className="text-xs text-red-300">
                {problem}
              </p>
            )}
            {results && <Results files={results} onDismiss={() => setResults(null)} />}
          </GlassPanel>
          <GlassPanel as="section" aria-labelledby="booklets-title" className="space-y-4 p-6">
            <h2 id="booklets-title" className={sectionTitle}>
              <i className="fa-solid fa-folder-open text-cyan-400" aria-hidden="true" /> Booklets (
              {u.counts.total})
            </h2>
            <p className="text-xs text-gray-400">
              {u.counts.completed} completed · {u.counts.in_review} in review · {u.counts.ready}{' '}
              ready for the SME · {u.counts.processing} processing ·{' '}
              {u.counts.needs_retake + u.counts.failed} need a retake
            </p>
            <Booklets unit={u} />
          </GlassPanel>
        </>
      )}
    </div>
  )
}
