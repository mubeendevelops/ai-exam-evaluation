import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError, TextField } from '../../components/public/fields'
import { SelectInput, TextArea } from '../../components/schema/controls'
import { Badge, GlassPanel, Modal, useToast, type Tone } from '../../components/ui'
import {
  CancelButton,
  ConfirmDialog,
  PrimaryButton,
  RowButton,
  SubmitInDialog,
  sectionTitle,
} from './controls'
import {
  grantsOf,
  isLocked,
  useAccounts,
  useEntitledSmes,
  useSubjects,
  type Account,
  type Subject,
  type WorkUnit,
} from './data'
import { REASON_MAX } from './TransferDialog'
import { SubjectChecklist } from './SubjectChecklist'
import { SubjectsPanel } from './SubjectsPanel'

/** What the account's status is called here: "Suspended" is the API's disabled (D161 wording). */
const STATUS: Record<Account['status'], { label: string; tone: Tone }> = {
  ACTIVE: { label: 'Active', tone: 'emerald' },
  DISABLED: { label: 'Suspended', tone: 'red' },
  PENDING: { label: 'Invited', tone: 'amber' },
}

const CONFIRM: Record<
  'suspend' | 'force-reset' | 'remove',
  { title: string; label: string; text: (name: string) => string }
> = {
  suspend: {
    title: 'Suspend this account?',
    label: 'Suspend account',
    text: (name) => `${name} will be signed out and cannot sign in until you resume the account.`,
  },
  'force-reset': {
    title: 'Require a new password?',
    label: 'Force reset',
    text: (name) => `${name} will have to choose a new password at the next sign-in.`,
  },
  remove: {
    title: 'Remove this SME?',
    label: 'Remove SME',
    text: (name) =>
      `${name} leaves the directory and can no longer sign in; the audit history stays. An SME who owns open units cannot be removed: transfer them first in Redistribute Workload.`,
  },
}

type Dialog =
  | { kind: 'add' }
  | { kind: 'subjects'; account: Account }
  | { kind: 'confirm'; action: 'suspend' | 'force-reset' | 'remove'; account: Account }
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

/** A removed subject in which the SME owns open units: each must move to an entitled SME. */
interface Removal {
  subject: Subject
  units: WorkUnit[]
}

async function openUnitsOf(userId: string, subjectId: string): Promise<WorkUnit[]> {
  const { data } = await api.GET('/api/v1/units', {
    params: { query: { owner_id: userId, subject_id: subjectId, state: 'open' } },
  })
  if (!data) throw new Error('units unavailable')
  return data
}

/** The new SME for one unit of a subject being removed: entitled SMEs other than this one. */
function UnitTarget({
  unit,
  leaving,
  value,
  onChange,
}: {
  unit: WorkUnit
  leaving: string
  value: string
  onChange: (ownerId: string) => void
}) {
  const smes = useEntitledSmes(unit.subject.id)
  const targets = (smes.data ?? []).filter((s) => s.id !== leaving)
  const none = smes.data !== undefined && targets.length === 0
  return (
    <SelectInput
      label={`New SME for ${unit.code}`}
      value={value}
      required
      disabled={none}
      hint={
        none
          ? 'No other active SME has this subject: grant it to someone first, or cancel.'
          : `${unit.paper.exam_code} · ${unit.year} · ${unit.counts.completed} of ${unit.counts.total} completed`
      }
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">Choose an SME…</option>
      {targets.map((s) => (
        <option key={s.id} value={s.id}>
          {s.name} ({s.email})
        </option>
      ))}
    </SelectInput>
  )
}

/**
 * The SME's subjects as a checklist. Removing a subject in which the SME owns open units adds a
 * step (P27, D163): the admin picks a new SME for each of those units, and the subject goes
 * with the transfers in one call (all or nothing), or the admin cancels.
 */
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
  const [removals, setRemovals] = useState<Removal[] | null>(null)
  const [targets, setTargets] = useState<Record<string, string>>({})
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const choices = subjects.filter((s) => !s.retired || before.has(s.id))
  const userId = account.user.id

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['accounts'] }),
      queryClient.invalidateQueries({ queryKey: ['units'] }),
    ])
  }

  async function apply(withUnits: readonly Removal[]): Promise<boolean> {
    const moving = new Map(withUnits.map((r) => [r.subject.id, r.units]))
    for (const subject of subjects) {
      const had = before.has(subject.id)
      const wants = chosen.has(subject.id)
      if (had === wants) continue
      const params = { path: { user_id: userId, subject_id: subject.id } }
      const units = moving.get(subject.id)
      const { error: err, response } = wants
        ? await api.POST('/api/v1/accounts/{user_id}/subjects/{subject_id}', { params })
        : units
          ? await api.POST('/api/v1/accounts/{user_id}/subjects/{subject_id}/revoke', {
              params,
              body: {
                transfers: units.map((u) => ({ unit_id: u.id, owner_id: targets[u.id] ?? '' })),
                reason: reason.trim(),
              },
            })
          : await api.DELETE('/api/v1/accounts/{user_id}/subjects/{subject_id}', { params })
      if (!response.ok) {
        setError(problemOf(err, 'Could not change the subjects.').message)
        await refresh()
        return false
      }
    }
    return true
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      let found = removals
      if (found === null) {
        const removed = subjects.filter((s) => before.has(s.id) && !chosen.has(s.id))
        found = []
        for (const subject of removed) {
          const units = await openUnitsOf(userId, subject.id)
          if (units.length > 0) found.push({ subject, units })
        }
        if (found.length > 0) {
          setRemovals(found)
          return
        }
      }
      if (await apply(found)) {
        const moved = found.reduce((n, r) => n + r.units.length, 0)
        toast.show(
          moved > 0
            ? `Subjects of ${account.user.display_name} saved; ${moved} unit${moved === 1 ? '' : 's'} transferred.`
            : `Subjects of ${account.user.display_name} saved.`,
        )
        await refresh()
        onClose()
      }
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  const step = removals !== null && removals.length > 0
  const ready =
    !step || (reason.trim() !== '' && removals.every((r) => r.units.every((u) => targets[u.id])))
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Edit subjects"
      title={account.user.display_name}
      description={
        step
          ? `${account.user.display_name} has open units in the subjects you are removing. Choose who takes each one; every booklet moves as it stands. Cancel to keep everything as it is.`
          : 'Everything this SME sees is limited to the ticked subjects.'
      }
      footer={
        <div className="flex justify-end gap-2">
          <CancelButton onClick={onClose}>Cancel</CancelButton>
          <SubmitInDialog form="edit-subjects-form" busy={busy} disabled={!ready}>
            {step ? 'Remove and transfer' : 'Save subjects'}
          </SubmitInDialog>
        </div>
      }
    >
      <form id="edit-subjects-form" onSubmit={save} className="space-y-4">
        {step ? (
          <>
            {removals.map((r) => (
              <fieldset key={r.subject.id} className="space-y-3">
                <legend className="mb-1 text-xs font-semibold text-white">
                  Open units in {r.subject.name}
                </legend>
                {r.units.map((u) => (
                  <UnitTarget
                    key={u.id}
                    unit={u}
                    leaving={userId}
                    value={targets[u.id] ?? ''}
                    onChange={(owner) => setTargets((t) => ({ ...t, [u.id]: owner }))}
                  />
                ))}
              </fieldset>
            ))}
            <TextArea
              label="Reason for the transfers"
              value={reason}
              required
              rows={2}
              maxLength={REASON_MAX}
              hint="Kept in the audit log; not sent in the emails."
              onChange={(e) => setReason(e.target.value)}
            />
          </>
        ) : (
          <SubjectChecklist subjects={choices} selected={chosen} onChange={setChosen} />
        )}
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
      action: 'suspend' | 'resume' | 'force-reset' | 'unlock' | 'remove'
      id: string
    }) => {
      const params = { path: { user_id: input.id } }
      const result =
        input.action === 'remove'
          ? await api.DELETE('/api/v1/accounts/{user_id}', { params })
          : input.action === 'suspend'
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
          remove: 'SME removed from the directory.',
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
                          <RowButton
                            onClick={() =>
                              setDialog({ kind: 'confirm', action: 'remove', account })
                            }
                          >
                            Remove
                          </RowButton>
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
        title={CONFIRM[confirming?.action ?? 'suspend'].title}
        confirmLabel={CONFIRM[confirming?.action ?? 'suspend'].label}
        onConfirm={() => {
          if (confirming) act.mutate({ action: confirming.action, id: confirming.account.user.id })
          setDialog(null)
        }}
      >
        {confirming ? CONFIRM[confirming.action].text(confirming.account.user.display_name) : ''}
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
