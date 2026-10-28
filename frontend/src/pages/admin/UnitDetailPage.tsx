import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { uploadToUnit } from '../../api/booklets'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError } from '../../components/public/fields'
import { Dropzone } from '../../components/evaluate/Dropzone'
import { STATUS } from '../../components/evaluate/model'
import { StatusBadge } from '../../components/evaluate/StatusBadge'
import { TextArea } from '../../components/schema/controls'
import { Badge, EmptyState, GlassPanel, Modal, useToast } from '../../components/ui'
import type { components } from '../../api/schema'
import { CancelButton, PrimaryButton, RowButton, SubmitInDialog, sectionTitle } from './controls'
import { UNIT_STATE, returnNote, type UploadedFile, type WorkUnitDetail } from './data'
import { REASON_MAX } from './TransferDialog'
import { RetakePanel } from './RetakePanel'

type UnmarkedReason = components['schemas']['UnmarkedOut']['reason']

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

const REASON: Record<UnmarkedReason, string> = {
  absent: 'Absent',
  unreadable: 'Unreadable',
  other: 'Other',
}

/**
 * P28 (D164): where the unit stands. Submitted: what the SME handed over (booklets not
 * marked, with their reasons) and "Return to SME" until the Governor freezes it. Frozen: who
 * signed it off. Open after a return: the note the SME was sent.
 */
function SubmissionPanel({ unit }: { unit: WorkUnitDetail }) {
  const [returning, setReturning] = useState(false)
  const state = UNIT_STATE[unit.state]
  const submission = unit.submission
  const note = returnNote(unit)
  if (unit.state === 'open' && !note) return null
  const students = new Map(unit.booklets.map((b) => [b.id, b.student]))
  return (
    <GlassPanel as="section" aria-labelledby="submission-title" className="space-y-3 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="submission-title" className={sectionTitle}>
          <i className="fa-solid fa-paper-plane text-amber-400" aria-hidden="true" /> Submission
          <Badge tone={state.tone}>{state.label}</Badge>
        </h2>
        {unit.state === 'submitted' && (
          <PrimaryButton onClick={() => setReturning(true)}>
            <i className="fa-solid fa-rotate-left" aria-hidden="true" /> Return to SME
          </PrimaryButton>
        )}
      </div>
      {unit.state === 'open' && (
        <p className="text-xs text-gray-300">
          Returned to {unit.owner.name} with the note: <q className="text-amber-200">{note}</q>
        </p>
      )}
      {unit.state !== 'open' && submission && (
        <>
          <p className="text-xs text-gray-300">
            Submitted by {submission.submitted_by.name} on{' '}
            {new Date(submission.submitted_at).toLocaleString()}.{' '}
            {unit.state === 'frozen' && unit.frozen
              ? `Frozen and signed off by ${unit.frozen.frozen_by.name} on ${new Date(unit.frozen.frozen_at).toLocaleString()}: nothing in the unit changes now.`
              : 'Nothing in the unit can be changed unless you return it, until the Governor freezes it.'}
          </p>
          {submission.unmarked.length > 0 && (
            <ul aria-label="Booklets not marked" className="space-y-1 text-xs">
              {submission.unmarked.map((m) => {
                const student = students.get(m.booklet_id)
                return (
                  <li key={m.booklet_id} className="text-gray-300">
                    <span className="font-mono text-gray-200">{student?.usn ?? 'Booklet'}</span>{' '}
                    {student?.name} · not marked: {REASON[m.reason]}
                    {m.note && ` (${m.note})`}
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}
      {returning && <ReturnDialog unit={unit} onClose={() => setReturning(false)} />}
    </GlassPanel>
  )
}

function ReturnDialog({ unit, onClose }: { unit: WorkUnitDetail; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await api.POST('/api/v1/units/{unit_id}/return', {
        params: { path: { unit_id: unit.id } },
        body: { note: note.trim(), expected_version: unit.version },
      })
      if (data) {
        toast.show(`${unit.code} returned; ${unit.owner.name} was emailed your note.`)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['unit', unit.id] }),
          queryClient.invalidateQueries({ queryKey: ['units'] }),
        ])
        onClose()
      } else setError(problemOf(err, 'Could not return the unit.').message)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Return to SME"
      title={`Return ${unit.code} to ${unit.owner.name}`}
      description="The unit opens again for its SME, who is emailed your note. They submit it again when done."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="return-form" busy={busy} disabled={!note.trim()}>
            Return unit
          </SubmitInDialog>
        </div>
      }
    >
      <form id="return-form" onSubmit={submit} className="space-y-4">
        <TextArea
          label="Note to the SME"
          value={note}
          required
          rows={3}
          maxLength={REASON_MAX}
          placeholder="e.g. recheck question 3 in every booklet"
          onChange={(e) => setNote(e.target.value)}
        />
        <FormError message={error} />
      </form>
    </Modal>
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
      {u && <SubmissionPanel unit={u} />}
      {u && u.state === 'open' && (
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
        </>
      )}
      {u && (
        <>
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
