import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError, TextField } from '../../components/public/fields'
import { SelectInput } from '../../components/schema/controls'
import { EmptyState, GlassPanel, Modal, useToast } from '../../components/ui'
import { CancelButton, PrimaryButton, SubmitInDialog, sectionTitle } from './controls'
import { useAccounts, useSubjects, useUnits, type UnitFilter, type WorkUnit } from './data'

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

/**
 * Create a unit (D162): the subject narrows the papers (fully linked, with an exam code) and the
 * SMEs (active and entitled to it, D160); the server checks both again.
 */
function CreateUnitDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const subjects = useSubjects()
  const [subjectId, setSubjectId] = useState('')
  const [paperId, setPaperId] = useState('')
  const [year, setYear] = useState('')
  const [ownerId, setOwnerId] = useState('')
  const [due, setDue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const papers = useQuery({
    queryKey: ['unit-papers', subjectId],
    enabled: subjectId !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/units/papers', {
        params: { query: { subject_id: subjectId } },
      })
      if (!data) throw new Error('papers unavailable')
      return data
    },
  })
  const smes = useQuery({
    queryKey: ['unit-smes', subjectId],
    enabled: subjectId !== '',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/units/smes', {
        params: { query: { subject_id: subjectId } },
      })
      if (!data) throw new Error('SMEs unavailable')
      return data
    },
  })

  function pickSubject(id: string) {
    setSubjectId(id)
    setPaperId('')
    setOwnerId('')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await api.POST('/api/v1/units', {
        body: { paper_id: paperId, year: year.trim(), owner_id: ownerId, due_date: due },
      })
      if (data) {
        toast.show(`Unit ${data.code} created; ${data.owner.name} was emailed.`)
        await queryClient.invalidateQueries({ queryKey: ['units'] })
        onClose()
      } else setError(problemOf(err, 'Could not create the unit.').message)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  const live = (subjects.data ?? []).filter((s) => !s.retired)
  const noPapers = papers.data !== undefined && papers.data.length === 0
  const noSmes = smes.data !== undefined && smes.data.length === 0
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Upload & Assign Papers"
      title="Create a unit"
      description="One exam, one student year, one subject: one paper and one SME."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog
            form="unit-form"
            busy={busy}
            disabled={!paperId || !ownerId || !year.trim() || !due}
          >
            Create unit
          </SubmitInDialog>
        </div>
      }
    >
      <form id="unit-form" onSubmit={submit} className="space-y-4">
        <SelectInput
          label="Subject"
          value={subjectId}
          required
          onChange={(e) => pickSubject(e.target.value)}
        >
          <option value="">Choose a subject…</option>
          {live.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.code})
            </option>
          ))}
        </SelectInput>
        <SelectInput
          label="Paper"
          value={paperId}
          required
          disabled={!subjectId || noPapers}
          hint={
            noPapers
              ? 'No paper of this subject is ready: an SME links every question and sets the exam code in the Schema Designer.'
              : 'Papers with every question linked and an exam code.'
          }
          onChange={(e) => setPaperId(e.target.value)}
        >
          <option value="">Choose a paper…</option>
          {papers.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.exam_code} · {p.title}
            </option>
          ))}
        </SelectInput>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="Student year"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            required
            maxLength={20}
            placeholder="2024"
            hint="The batch, for example 2024."
          />
          <TextField
            label="Due date"
            type="date"
            value={due}
            min={today()}
            onChange={(e) => setDue(e.target.value)}
            required
          />
        </div>
        <SelectInput
          label="SME"
          value={ownerId}
          required
          disabled={!subjectId || noSmes}
          hint={
            noSmes
              ? 'No active SME has this subject: grant it in the SME Directory first.'
              : 'Only active SMEs who have this subject are offered. They are emailed.'
          }
          onChange={(e) => setOwnerId(e.target.value)}
        >
          <option value="">Choose an SME…</option>
          {smes.data?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.email})
            </option>
          ))}
        </SelectInput>
        <FormError message={error} />
      </form>
    </Modal>
  )
}

function Progress({ unit }: { unit: WorkUnit }) {
  const c = unit.counts
  if (c.total === 0) return <span className="text-gray-500">No booklets yet</span>
  return (
    <span className="text-gray-300">
      {c.completed} of {c.total} completed
      {c.needs_retake + c.failed > 0 && (
        <span className="ml-1 text-amber-300">· {c.needs_retake + c.failed} need a retake</span>
      )}
    </span>
  )
}

function UnitsTable() {
  const subjects = useSubjects()
  const accounts = useAccounts()
  const [filter, setFilter] = useState<UnitFilter>({})
  const units = useUnits(filter)
  const smes = (accounts.data ?? []).filter((a) => a.user.role === 'sme')
  const set = (key: keyof UnitFilter, value: string) =>
    setFilter((f) => {
      const next = { ...f }
      if (value) Object.assign(next, { [key]: value })
      else delete next[key]
      return next
    })

  return (
    <GlassPanel as="section" aria-labelledby="units-title" className="space-y-4 p-6">
      <h2 id="units-title" className={sectionTitle}>
        <i className="fa-solid fa-layer-group text-cyan-400" aria-hidden="true" /> Units of work
      </h2>
      <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-3">
        <SelectInput
          label="Subject"
          value={filter.subject_id ?? ''}
          onChange={(e) => set('subject_id', e.target.value)}
        >
          <option value="">All subjects</option>
          {subjects.data?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </SelectInput>
        <SelectInput
          label="SME"
          value={filter.owner_id ?? ''}
          onChange={(e) => set('owner_id', e.target.value)}
        >
          <option value="">All SMEs</option>
          {smes.map((a) => (
            <option key={a.user.id} value={a.user.id}>
              {a.user.display_name}
            </option>
          ))}
        </SelectInput>
        <SelectInput
          label="State"
          value={filter.state ?? ''}
          onChange={(e) => set('state', e.target.value)}
        >
          <option value="">Any state</option>
          <option value="open">Open</option>
        </SelectInput>
      </div>
      {units.isError && <FormError message="Could not load the units." />}
      {units.data && units.data.length === 0 && (
        <EmptyState icon="fa-solid fa-layer-group" title="No units yet">
          Create a unit to assign a paper to an SME, then upload its booklets.
        </EmptyState>
      )}
      {units.data && units.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Units of work</caption>
            <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
              <tr>
                <th scope="col" className="pr-4 pb-2">
                  Unit
                </th>
                <th scope="col" className="pr-4 pb-2">
                  Paper
                </th>
                <th scope="col" className="pr-4 pb-2">
                  Subject
                </th>
                <th scope="col" className="pr-4 pb-2">
                  Year
                </th>
                <th scope="col" className="pr-4 pb-2">
                  SME
                </th>
                <th scope="col" className="pr-4 pb-2">
                  Booklets
                </th>
                <th scope="col" className="pb-2">
                  Due
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {units.data.map((u) => (
                <tr key={u.id}>
                  <th scope="row" className="py-2 pr-4 text-left">
                    <Link
                      to={`/admin/upload/units/${u.id}`}
                      className="font-mono font-semibold text-cyan-300 hover:text-white"
                    >
                      {u.code}
                    </Link>
                  </th>
                  <td className="py-2 pr-4 text-gray-200">
                    <span className="block font-mono text-[11px] text-purple-300">
                      {u.paper.exam_code}
                    </span>
                    {u.paper.title}
                  </td>
                  <td className="py-2 pr-4 text-gray-300">{u.subject.name}</td>
                  <td className="py-2 pr-4 text-gray-300">{u.year}</td>
                  <td className="py-2 pr-4 text-gray-300">{u.owner.name}</td>
                  <td className="py-2 pr-4">
                    <Progress unit={u} />
                  </td>
                  <td className="py-2 text-gray-300">{u.due_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </GlassPanel>
  )
}

/**
 * `/admin/upload`, Upload & Assign Papers (P26): the units of work and "Create unit"; each
 * unit's page takes its booklets. The roster (Students) is a sub-page.
 */
export default function UnitsPage() {
  const [creating, setCreating] = useState(false)
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-file-arrow-up"
        iconTone="text-cyan-400"
        title="Upload & Assign Papers"
        description="Make a unit for each exam, student year and subject, assign it to an SME, and upload its booklets."
      >
        <div className="flex flex-wrap items-center gap-3">
          <Link
            to="/admin/upload/students"
            className="inline-flex items-center gap-2 text-xs font-semibold text-purple-300 hover:text-white"
          >
            <i className="fa-solid fa-user-graduate" aria-hidden="true" /> Students
          </Link>
          <PrimaryButton onClick={() => setCreating(true)}>
            <i className="fa-solid fa-plus" aria-hidden="true" /> Create unit
          </PrimaryButton>
        </div>
      </PageBanner>
      <UnitsTable />
      {creating && <CreateUnitDialog onClose={() => setCreating(false)} />}
    </div>
  )
}
