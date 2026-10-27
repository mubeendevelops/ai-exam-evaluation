import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { FormError, TextField } from '../../components/public/fields'
import { Badge, GlassPanel, Modal, useToast } from '../../components/ui'
import {
  CancelButton,
  ConfirmDialog,
  PrimaryButton,
  RowButton,
  SubmitInDialog,
  sectionTitle,
} from './controls'
import { useSubjects, type Subject } from './data'

type Dialog =
  | { kind: 'add' }
  | { kind: 'rename'; subject: Subject }
  | { kind: 'retire'; subject: Subject }
  | null

function SubjectDialog({ subject, onClose }: { subject: Subject | null; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [code, setCode] = useState(subject?.code ?? '')
  const [name, setName] = useState(subject?.name ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const body = { code: code.trim(), name: name.trim() }
    try {
      const { error: err, response } = subject
        ? await api.PUT('/api/v1/subjects/{subject_id}', {
            params: { path: { subject_id: subject.id } },
            body,
          })
        : await api.POST('/api/v1/subjects', { body })
      if (response.ok) {
        toast.show(subject ? 'Subject renamed.' : `Subject ${body.name} added.`)
        await queryClient.invalidateQueries({ queryKey: ['subjects'] })
        onClose()
      } else setError(problemOf(err, 'Could not save the subject.').message)
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
      eyebrow="Subjects"
      title={subject ? 'Rename subject' : 'Add subject'}
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="subject-form" busy={busy}>
            {subject ? 'Save subject' : 'Add subject'}
          </SubmitInDialog>
        </div>
      }
    >
      <form id="subject-form" onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <TextField
            label="Code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
            maxLength={40}
            placeholder="PHY-501"
          />
          <div className="sm:col-span-2">
            <TextField
              label="Subject name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={200}
              placeholder="Physics"
            />
          </div>
        </div>
        <FormError message={error} />
      </form>
    </Modal>
  )
}

/** Subjects of the college: the admin adds, renames and retires them (D160, D181). */
export function SubjectsPanel() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const subjects = useSubjects()
  const [dialog, setDialog] = useState<Dialog>(null)

  async function retire(subject: Subject) {
    setDialog(null)
    try {
      const { error, response } = await api.POST('/api/v1/subjects/{subject_id}/retire', {
        params: { path: { subject_id: subject.id } },
      })
      if (response.ok) {
        toast.show(`Subject ${subject.name} retired.`)
        await queryClient.invalidateQueries({ queryKey: ['subjects'] })
      } else toast.show(problemOf(error, 'Could not retire the subject.').message, 'red')
    } catch {
      toast.show(NETWORK_PROBLEM, 'red')
    }
  }

  const retiring = dialog?.kind === 'retire' ? dialog.subject : null
  return (
    <>
      <GlassPanel as="section" aria-labelledby="subjects-title" className="space-y-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="subjects-title" className={sectionTitle}>
            <i className="fa-solid fa-book text-cyan-400" aria-hidden="true" /> Subjects
          </h2>
          <PrimaryButton onClick={() => setDialog({ kind: 'add' })}>
            <i className="fa-solid fa-plus" aria-hidden="true" /> Add subject
          </PrimaryButton>
        </div>
        {subjects.isPending && <p className="text-xs text-gray-400">Loading subjects…</p>}
        {subjects.isError && (
          <FormError message="Could not load the subjects. Reload the page to try again." />
        )}
        {subjects.data && subjects.data.length === 0 && (
          <p className="text-xs text-gray-400">No subjects yet. Add the first one.</p>
        )}
        {subjects.data && subjects.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">Subjects of this college</caption>
              <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
                <tr>
                  <th scope="col" className="pb-2 pr-4">
                    Code
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Name
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Status
                  </th>
                  <th scope="col" className="pb-2 text-right">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {subjects.data.map((subject) => (
                  <tr key={subject.id}>
                    <td className="py-2.5 pr-4 font-mono text-cyan-300">{subject.code}</td>
                    <th scope="row" className="py-2.5 pr-4 text-left font-semibold text-white">
                      {subject.name}
                    </th>
                    <td className="py-2.5 pr-4">
                      <Badge tone={subject.retired ? 'gray' : 'emerald'}>
                        {subject.retired ? 'Retired' : 'Active'}
                      </Badge>
                    </td>
                    <td className="py-2.5 text-right">
                      {!subject.retired && (
                        <div className="flex justify-end gap-1.5">
                          <RowButton onClick={() => setDialog({ kind: 'rename', subject })}>
                            Rename
                          </RowButton>
                          <RowButton onClick={() => setDialog({ kind: 'retire', subject })}>
                            Retire
                          </RowButton>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassPanel>

      {dialog?.kind === 'add' && <SubjectDialog subject={null} onClose={() => setDialog(null)} />}
      {dialog?.kind === 'rename' && (
        <SubjectDialog subject={dialog.subject} onClose={() => setDialog(null)} />
      )}
      <ConfirmDialog
        open={retiring !== null}
        onClose={() => setDialog(null)}
        title="Retire this subject?"
        confirmLabel="Retire subject"
        onConfirm={() => retiring && void retire(retiring)}
      >
        {`${retiring?.name ?? 'This subject'} can no longer be granted to SMEs or used for a new paper. Its questions, papers and existing grants stay.`}
      </ConfirmDialog>
    </>
  )
}
