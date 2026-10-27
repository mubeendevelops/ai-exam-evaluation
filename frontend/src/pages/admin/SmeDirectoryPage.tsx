import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError, TextField } from '../../components/public/fields'
import { Badge, GlassPanel, Modal, useToast, type Tone } from '../../components/ui'
import {
  CancelButton,
  ConfirmDialog,
  PrimaryButton,
  RowButton,
  SubmitInDialog,
  sectionTitle,
} from './controls'
import { grantsOf, isLocked, useAccounts, useSubjects, type Account, type Subject } from './data'
import { SubjectChecklist } from './SubjectChecklist'
import { SubjectsPanel } from './SubjectsPanel'

/** What the account's status is called here: "Suspended" is the API's disabled (D161 wording). */
const STATUS: Record<Account['status'], { label: string; tone: Tone }> = {
  ACTIVE: { label: 'Active', tone: 'emerald' },
  DISABLED: { label: 'Suspended', tone: 'red' },
  PENDING: { label: 'Invited', tone: 'amber' },
}

type Dialog =
  | { kind: 'add' }
  | { kind: 'subjects'; account: Account }
  | { kind: 'confirm'; action: 'suspend' | 'force-reset'; account: Account }
  | null

// ---- add an SME ----------------------------------------------------------------------------

function AddSmeDialog({
  subjects,
  onClose,
}: {
  subjects: readonly Subject[]
  onClose: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)
  const choices = subjects.filter((s) => !s.retired)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (chosen.size === 0) {
      setError({ message: 'Choose at least one subject.', reasons: [] })
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } = await api.POST('/api/v1/accounts', {
        body: { display_name: name.trim(), email: email.trim(), subject_ids: [...chosen] },
      })
      if (response.ok) {
        toast.show(`Invitation sent to ${email.trim()}.`)
        await queryClient.invalidateQueries({ queryKey: ['accounts'] })
        onClose()
      } else setError(problemOf(err, 'Could not add this SME.'))
    } catch {
      setError({ message: NETWORK_PROBLEM, reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="SME Directory"
      title="Add SME"
      description="The SME gets an invitation by email and sees only the subjects chosen here."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="add-sme-form" busy={busy}>
            Send invitation
          </SubmitInDialog>
        </div>
      }
    >
      <form id="add-sme-form" onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="Full Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="Prof. Asha Rao"
          />
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            placeholder="sme@institution.edu"
          />
        </div>
        <SubjectChecklist subjects={choices} selected={chosen} onChange={setChosen} />
        <FormError message={error?.message ?? null} reasons={error?.reasons} />
      </form>
    </Modal>
  )
}

// ---- edit an SME's subjects ------------------------------------------------------------------

function EditSubjectsDialog({
  account,
  subjects,
  onClose,
}: {
  account: Account
  subjects: readonly Subject[]
  onClose: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const before = new Set(grantsOf(account))
  const [chosen, setChosen] = useState<Set<string>>(new Set(before))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const choices = subjects.filter((s) => !s.retired || before.has(s.id))

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      for (const subject of subjects) {
        const had = before.has(subject.id)
        const wants = chosen.has(subject.id)
        if (had === wants) continue
        const params = { path: { user_id: account.user.id, subject_id: subject.id } }
        const { error: err, response } = wants
          ? await api.POST('/api/v1/accounts/{user_id}/subjects/{subject_id}', { params })
          : await api.DELETE('/api/v1/accounts/{user_id}/subjects/{subject_id}', { params })
        if (!response.ok) {
          setError(problemOf(err, 'Could not change the subjects.').message)
          await queryClient.invalidateQueries({ queryKey: ['accounts'] })
          return
        }
      }
      toast.show(`Subjects of ${account.user.display_name} saved.`)
      await queryClient.invalidateQueries({ queryKey: ['accounts'] })
      onClose()
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
      eyebrow="Edit subjects"
      title={account.user.display_name}
      description="Everything this SME sees is limited to the ticked subjects."
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="edit-subjects-form" busy={busy}>
            Save subjects
          </SubmitInDialog>
        </div>
      }
    >
      <form id="edit-subjects-form" onSubmit={save} className="space-y-4">
        <SubjectChecklist subjects={choices} selected={chosen} onChange={setChosen} />
        <FormError message={error} />
      </form>
    </Modal>
  )
}

// ---- the directory ---------------------------------------------------------------------------

function Directory() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const accounts = useAccounts()
  const subjects = useSubjects()
  const [query, setQuery] = useState('')
  const [dialog, setDialog] = useState<Dialog>(null)

  const act = useMutation({
    mutationFn: async (input: {
      action: 'suspend' | 'resume' | 'force-reset' | 'unlock'
      id: string
    }) => {
      const params = { path: { user_id: input.id } }
      const result =
        input.action === 'suspend'
          ? await api.POST('/api/v1/accounts/{user_id}/disable', { params })
          : input.action === 'resume'
            ? await api.POST('/api/v1/accounts/{user_id}/enable', { params })
            : input.action === 'force-reset'
              ? await api.POST('/api/v1/accounts/{user_id}/force-reset', { params })
              : await api.POST('/api/v1/accounts/{user_id}/unlock', { params })
      if (!result.response.ok)
        throw new Error(problemOf(result.error, 'That did not work.').message)
    },
    onSuccess: async (_data, input) => {
      toast.show(
        {
          suspend: 'Account suspended.',
          resume: 'Account resumed.',
          'force-reset': 'Password reset required at the next sign-in.',
          unlock: 'Account unlocked.',
        }[input.action],
      )
      await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    },
    onError: (error) => toast.show(error instanceof Error ? error.message : NETWORK_PROBLEM, 'red'),
  })

  const subjectById = new Map((subjects.data ?? []).map((s) => [s.id, s]))
  const needle = query.trim().toLowerCase()
  const smes = (accounts.data ?? [])
    .filter((a) => a.user.role === 'sme')
    .filter((a) => {
      if (!needle) return true
      const names = grantsOf(a).map((id) => subjectById.get(id)?.name ?? '')
      return [a.user.display_name, a.user.email, ...names].some((text) =>
        text.toLowerCase().includes(needle),
      )
    })
  const confirming = dialog?.kind === 'confirm' ? dialog : null

  return (
    <>
      <GlassPanel as="section" aria-labelledby="sme-title" className="space-y-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="sme-title" className={sectionTitle}>
            <i className="fa-solid fa-chalkboard-user text-purple-400" aria-hidden="true" /> SME
            Directory
          </h2>
          <PrimaryButton onClick={() => setDialog({ kind: 'add' })} disabled={!subjects.data}>
            <i className="fa-solid fa-user-plus" aria-hidden="true" /> Add SME
          </PrimaryButton>
        </div>
        <div className="max-w-md">
          <TextField
            label="Search SMEs"
            icon="fa-solid fa-magnifying-glass"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={100}
            placeholder="Name, email or subject"
          />
        </div>

        {accounts.isPending && <p className="text-xs text-gray-400">Loading SMEs…</p>}
        {accounts.isError && (
          <FormError message="Could not load the SMEs. Reload the page to try again." />
        )}
        {accounts.data && smes.length === 0 && (
          <p className="text-xs text-gray-400">
            {needle ? 'No SME matches.' : 'No SMEs yet. Add one to send an invitation.'}
          </p>
        )}
        {smes.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">SMEs of this college</caption>
              <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
                <tr>
                  <th scope="col" className="pb-2 pr-4">
                    Name
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Email
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Subjects
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
                {smes.map((account) => {
                  const { user } = account
                  const status = STATUS[account.status]
                  return (
                    <tr key={user.id}>
                      <th scope="row" className="py-2.5 pr-4 text-left font-semibold text-white">
                        {user.display_name}
                      </th>
                      <td className="py-2.5 pr-4 text-gray-300">{user.email}</td>
                      <td className="py-2.5 pr-4">
                        <ul className="flex flex-wrap gap-1" aria-label="Subjects">
                          {grantsOf(account).length === 0 && (
                            <li className="text-gray-500">None</li>
                          )}
                          {grantsOf(account).map((id) => {
                            const subject = subjectById.get(id)
                            return (
                              <li key={id}>
                                <Badge tone={subject?.retired ? 'gray' : 'purple'}>
                                  {subject?.name ?? '…'}
                                </Badge>
                              </li>
                            )
                          })}
                        </ul>
                      </td>
                      <td className="space-x-1 py-2.5 pr-4">
                        <Badge tone={status.tone}>{status.label}</Badge>
                        {isLocked(account) && <Badge tone="red">locked</Badge>}
                        {account.force_reset && <Badge tone="amber">reset required</Badge>}
                      </td>
                      <td className="py-2.5 text-right">
                        <div className="flex flex-wrap justify-end gap-1.5">
                          <RowButton
                            disabled={!subjects.data}
                            onClick={() => setDialog({ kind: 'subjects', account })}
                          >
                            Edit subjects
                          </RowButton>
                          {account.status === 'DISABLED' ? (
                            <RowButton
                              disabled={act.isPending}
                              onClick={() => act.mutate({ action: 'resume', id: user.id })}
                            >
                              Resume
                            </RowButton>
                          ) : (
                            <RowButton
                              onClick={() =>
                                setDialog({ kind: 'confirm', action: 'suspend', account })
                              }
                            >
                              Suspend
                            </RowButton>
                          )}
                          <RowButton
                            onClick={() =>
                              setDialog({ kind: 'confirm', action: 'force-reset', account })
                            }
                          >
                            Force reset
                          </RowButton>
                          {isLocked(account) && (
                            <RowButton
                              disabled={act.isPending}
                              onClick={() => act.mutate({ action: 'unlock', id: user.id })}
                            >
                              Unlock
                            </RowButton>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </GlassPanel>

      {dialog?.kind === 'add' && subjects.data && (
        <AddSmeDialog subjects={subjects.data} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === 'subjects' && subjects.data && (
        <EditSubjectsDialog
          account={dialog.account}
          subjects={subjects.data}
          onClose={() => setDialog(null)}
        />
      )}
      <ConfirmDialog
        open={confirming !== null}
        onClose={() => setDialog(null)}
        title={
          confirming?.action === 'suspend' ? 'Suspend this account?' : 'Require a new password?'
        }
        confirmLabel={confirming?.action === 'suspend' ? 'Suspend account' : 'Force reset'}
        onConfirm={() => {
          if (confirming) act.mutate({ action: confirming.action, id: confirming.account.user.id })
          setDialog(null)
        }}
      >
        {confirming?.action === 'suspend'
          ? `${confirming.account.user.display_name} will be signed out and cannot sign in until you resume the account.`
          : `${confirming?.account.user.display_name ?? 'This SME'} will have to choose a new password at the next sign-in.`}
      </ConfirmDialog>
    </>
  )
}

/** `/admin/smes`: who may evaluate which subjects (D160) and the subject list itself (D181). */
export default function SmeDirectoryPage() {
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-users"
        iconTone="text-purple-400"
        title="SME Directory & Subjects"
        description="Invite SMEs, choose the subjects each may work on, and keep the subject list."
      />
      <Directory />
      <SubjectsPanel />
    </div>
  )
}
