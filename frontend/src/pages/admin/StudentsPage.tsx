import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError, TextField } from '../../components/public/fields'
import { GlassPanel, Modal, useToast } from '../../components/ui'
import { useDebounced } from '../../hooks/useDebounced'
import { CancelButton, PrimaryButton, RowButton, SubmitInDialog, sectionTitle } from './controls'
import type { Student } from './data'
import { RosterImport } from './RosterImport'

const LIMIT = 50

/** Add a student, or correct one (`student` set): name, USN, class/section. */
function StudentDialog({ student, onClose }: { student: Student | null; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [name, setName] = useState(student?.name ?? '')
  const [usn, setUsn] = useState(student?.usn ?? '')
  const [classSection, setClassSection] = useState(student?.class_section ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const body = { name: name.trim(), usn: usn.trim(), class_section: classSection.trim() }
    try {
      const { error: err, response } = student
        ? await api.PUT('/api/v1/students/{student_id}', {
            params: { path: { student_id: student.id } },
            body,
          })
        : await api.POST('/api/v1/students', { body })
      if (response.ok) {
        toast.show(student ? 'Student saved.' : 'Student added.')
        await queryClient.invalidateQueries({ queryKey: ['students'] })
        onClose()
      } else setError(problemOf(err, 'Could not save the student.').message)
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
      eyebrow="Student roster"
      title={student ? 'Edit student' : 'Add student'}
      description={
        student
          ? 'Booklets already filed under this student follow the corrected details.'
          : undefined
      }
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="student-form" busy={busy}>
            {student ? 'Save student' : 'Add student'}
          </SubmitInDialog>
        </div>
      }
    >
      <form id="student-form" onSubmit={submit} className="space-y-4">
        <TextField
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={200}
          placeholder="Asha Rao"
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="Roll number (USN)"
            value={usn}
            onChange={(e) => setUsn(e.target.value)}
            required
            maxLength={40}
            placeholder="1AB21CS001"
            hint="Letters and digits, 4 to 20; stored in upper case."
          />
          <TextField
            label="Class / section"
            value={classSection}
            onChange={(e) => setClassSection(e.target.value)}
            maxLength={80}
            placeholder="CSE-A"
          />
        </div>
        <FormError message={error} />
      </form>
    </Modal>
  )
}

function Roster() {
  const [q, setQ] = useState('')
  const [dialog, setDialog] = useState<{ student: Student | null } | null>(null)
  const term = useDebounced(q.trim(), 250)
  const students = useQuery({
    queryKey: ['students', term],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/students', {
        params: { query: { q: term, limit: LIMIT } },
      })
      if (!data) throw new Error('students unavailable')
      return data
    },
  })

  return (
    <>
      <GlassPanel as="section" aria-labelledby="roster-title" className="space-y-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="roster-title" className={sectionTitle}>
            <i className="fa-solid fa-users text-cyan-400" aria-hidden="true" /> Student roster
          </h2>
          <PrimaryButton onClick={() => setDialog({ student: null })}>
            <i className="fa-solid fa-user-plus" aria-hidden="true" /> Add student
          </PrimaryButton>
        </div>
        <div className="max-w-md">
          <TextField
            label="Search by USN or name"
            icon="fa-solid fa-magnifying-glass"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            maxLength={100}
            placeholder="1AB21 or Asha"
          />
        </div>
        {students.isError && <FormError message="Could not load the roster." />}
        {students.data && students.data.length === 0 && (
          <p className="text-xs text-gray-400">
            {term ? 'No student matches.' : 'The roster is empty. Add a student or upload a CSV.'}
          </p>
        )}
        {students.data && students.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">Students on the roster</caption>
              <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
                <tr>
                  <th scope="col" className="pb-2 pr-4">
                    USN
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Name
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Class / section
                  </th>
                  <th scope="col" className="pb-2 text-right">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {students.data.map((student) => (
                  <tr key={student.id}>
                    <td className="py-2 pr-4 font-mono text-cyan-300">{student.usn}</td>
                    <th scope="row" className="py-2 pr-4 text-left font-semibold text-white">
                      {student.name}
                    </th>
                    <td className="py-2 pr-4 text-gray-300">{student.class_section}</td>
                    <td className="py-2 text-right">
                      <RowButton onClick={() => setDialog({ student })}>Edit</RowButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {students.data && students.data.length === LIMIT && (
          <p className="text-[11px] text-gray-400">
            Showing the first {LIMIT}. Search by USN or name to narrow the list.
          </p>
        )}
      </GlassPanel>
      {dialog && <StudentDialog student={dialog.student} onClose={() => setDialog(null)} />}
    </>
  )
}

/**
 * `/admin/upload/students`: the roster, a sub-page of Upload & Assign Papers. Uploading needs a
 * student on the roster, and a booklet filed under a wrong roll number is fixed here (P26).
 */
export default function StudentsPage() {
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-user-graduate"
        iconTone="text-cyan-400"
        title="Students"
        description="The roster booklets are filed under. Add or correct a student, or import a CSV."
      >
        <Link
          to="/admin/upload"
          className="inline-flex items-center gap-2 text-xs font-semibold text-purple-300 hover:text-white"
        >
          <i className="fa-solid fa-arrow-left" aria-hidden="true" /> Upload & Assign Papers
        </Link>
      </PageBanner>
      <Roster />
      <GlassPanel as="section" aria-labelledby="import-title" className="space-y-4 p-6">
        <h2 id="import-title" className={sectionTitle}>
          <i className="fa-solid fa-file-csv text-purple-400" aria-hidden="true" /> Import
        </h2>
        <div className="max-w-xl">
          <RosterImport />
        </div>
      </GlassPanel>
    </div>
  )
}
