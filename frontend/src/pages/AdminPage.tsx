import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { NETWORK_PROBLEM, problemOf } from '../api/errors'
import type { components } from '../api/schema'
import { PageBanner } from '../components/app/PageBanner'
import { FormError, SubmitButton, TextField } from '../components/public/fields'
import { Badge, GlassPanel, Modal, useToast, type Tone } from '../components/ui'
import { useDebounced } from '../hooks/useDebounced'

type Account = components['schemas']['AccountOut']
type RosterReport = components['schemas']['RosterReportOut']

const STATUS_TONE: Record<string, Tone> = { ACTIVE: 'emerald', PENDING: 'amber', DISABLED: 'red' }

function isLocked(account: Account): boolean {
  return account.locked_until !== null && new Date(account.locked_until) > new Date()
}

type Action = 'disable' | 'force-reset'

// ---- teachers ---------------------------------------------------------------------------

function InviteForm() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; reasons: string[] } | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { error: err, response } = await api.POST('/api/v1/accounts', {
        body: { display_name: name.trim(), email: email.trim() },
      })
      if (response.ok) {
        toast.show(`Invitation sent to ${email.trim()}.`)
        setName('')
        setEmail('')
        await queryClient.invalidateQueries({ queryKey: ['accounts'] })
      } else setError(problemOf(err, 'Could not invite this teacher.'))
    } catch {
      setError({ message: NETWORK_PROBLEM, reasons: [] })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Invite a teacher">
      <h3 className="text-sm font-bold tracking-wider text-purple-400 uppercase">
        Invite a teacher
      </h3>
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
          placeholder="teacher@institution.edu"
        />
      </div>
      <FormError message={error?.message ?? null} reasons={error?.reasons} />
      <div className="max-w-xs">
        <SubmitButton busy={busy}>Send invitation</SubmitButton>
      </div>
    </form>
  )
}

function TeachersSection() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<{ action: Action; account: Account } | null>(null)

  const accounts = useQuery({
    queryKey: ['accounts'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/accounts')
      if (!data) throw new Error('accounts unavailable')
      return data
    },
  })

  const act = useMutation({
    mutationFn: async (input: {
      action: 'disable' | 'enable' | 'force-reset' | 'unlock'
      id: string
    }) => {
      const params = { path: { user_id: input.id } }
      const result =
        input.action === 'disable'
          ? await api.POST('/api/v1/accounts/{user_id}/disable', { params })
          : input.action === 'enable'
            ? await api.POST('/api/v1/accounts/{user_id}/enable', { params })
            : input.action === 'force-reset'
              ? await api.POST('/api/v1/accounts/{user_id}/force-reset', { params })
              : await api.POST('/api/v1/accounts/{user_id}/unlock', { params })
      if (!result.response.ok)
        throw new Error(problemOf(result.error, 'That did not work.').message)
    },
    onSuccess: async (_data, input) => {
      const text = {
        disable: 'Account disabled.',
        enable: 'Account enabled.',
        'force-reset': 'Password reset required at the next sign-in.',
        unlock: 'Account unlocked.',
      }[input.action]
      toast.show(text)
      await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    },
    onError: (error) => toast.show(error instanceof Error ? error.message : NETWORK_PROBLEM, 'red'),
  })

  const rowButton =
    'rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1 text-[11px] font-semibold text-gray-200 hover:bg-gray-700 disabled:opacity-50'

  return (
    <GlassPanel as="section" aria-labelledby="accounts-title" className="space-y-6 p-6">
      <h2 id="accounts-title" className="flex items-center gap-2 text-lg font-bold text-white">
        <i className="fa-solid fa-chalkboard-user text-purple-400" aria-hidden="true" /> Accounts
      </h2>

      {accounts.isPending && <p className="text-xs text-gray-400">Loading accounts…</p>}
      {accounts.isError && (
        <FormError message="Could not load the accounts. Reload the page to try again." />
      )}
      {accounts.data && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
              <tr>
                <th scope="col" className="pb-2 pr-4">
                  Name
                </th>
                <th scope="col" className="pb-2 pr-4">
                  Email
                </th>
                <th scope="col" className="pb-2 pr-4">
                  Role
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
              {accounts.data.map((account) => {
                const { user } = account
                const teacher = user.role === 'teacher'
                return (
                  <tr key={user.id}>
                    <td className="py-2.5 pr-4 font-semibold text-white">{user.display_name}</td>
                    <td className="py-2.5 pr-4 text-gray-300">{user.email}</td>
                    <td className="py-2.5 pr-4">
                      <Badge tone={teacher ? 'purple' : 'amber'}>{user.role}</Badge>
                    </td>
                    <td className="space-x-1 py-2.5 pr-4">
                      <Badge tone={STATUS_TONE[account.status] ?? 'gray'}>
                        {account.status.toLowerCase()}
                      </Badge>
                      {isLocked(account) && <Badge tone="red">locked</Badge>}
                      {account.force_reset && <Badge tone="amber">reset required</Badge>}
                    </td>
                    <td className="py-2.5 text-right">
                      {teacher && (
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {account.status === 'DISABLED' ? (
                            <button
                              type="button"
                              className={rowButton}
                              disabled={act.isPending}
                              onClick={() => act.mutate({ action: 'enable', id: user.id })}
                            >
                              Enable
                            </button>
                          ) : (
                            <button
                              type="button"
                              className={rowButton}
                              onClick={() => setPending({ action: 'disable', account })}
                            >
                              Disable
                            </button>
                          )}
                          <button
                            type="button"
                            className={rowButton}
                            onClick={() => setPending({ action: 'force-reset', account })}
                          >
                            Force reset
                          </button>
                          {isLocked(account) && (
                            <button
                              type="button"
                              className={rowButton}
                              disabled={act.isPending}
                              onClick={() => act.mutate({ action: 'unlock', id: user.id })}
                            >
                              Unlock
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <InviteForm />

      <Modal
        open={pending !== null}
        onClose={() => setPending(null)}
        eyebrow="Confirm"
        title={pending?.action === 'disable' ? 'Disable this account?' : 'Require a new password?'}
        footer={
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setPending(null)}
              className="rounded-xl bg-gray-800 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-gray-700"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                if (pending) act.mutate({ action: pending.action, id: pending.account.user.id })
                setPending(null)
              }}
              className="rounded-xl bg-red-600 px-4 py-2 text-xs font-bold text-white hover:bg-red-500"
            >
              {pending?.action === 'disable' ? 'Disable account' : 'Force reset'}
            </button>
          </div>
        }
      >
        <p className="text-sm text-gray-300">
          {pending?.action === 'disable'
            ? `${pending.account.user.display_name} will be signed out and cannot sign in until you enable the account again.`
            : `${pending?.account.user.display_name ?? 'This teacher'} will have to choose a new password at the next sign-in.`}
        </p>
      </Modal>
    </GlassPanel>
  )
}

// ---- roster -----------------------------------------------------------------------------

const MAX_BYTES = 1_000_000

function RosterImport() {
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<RosterReport | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file) return
    if (file.size > MAX_BYTES) {
      setError('The file is larger than 1 MB.')
      return
    }
    setBusy(true)
    setError(null)
    setReport(null)
    try {
      const {
        data,
        error: err,
        response,
      } = await api.POST('/api/v1/roster/import', {
        body: await file.text(),
        bodySerializer: (body) => body,
        headers: { 'Content-Type': 'text/csv' },
      })
      if (data) {
        setReport(data)
        if (data.imported) await queryClient.invalidateQueries({ queryKey: ['students'] })
      } else if (response.status === 415 || response.status === 413) {
        setError(problemOf(err, 'The file was not accepted.').message)
      } else setError(problemOf(err, 'The roster was not imported.').message)
    } catch {
      setError(NETWORK_PROBLEM)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Import the roster">
      <h3 className="text-sm font-bold tracking-wider text-purple-400 uppercase">Upload roster</h3>
      <p className="text-xs text-gray-400">
        CSV with a header row:{' '}
        <span className="font-mono text-gray-300">name, usn, class/section</span>. UTF-8, up to 1 MB
        and 5000 rows. Students are matched by USN, so uploading again updates them. If any line has
        an error, nothing is imported.
      </p>
      <div>
        <label
          htmlFor="roster-file"
          className="mb-1 block text-xs font-bold tracking-wider text-gray-300 uppercase"
        >
          Roster file
        </label>
        <input
          id="roster-file"
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null)
            setReport(null)
            setError(null)
          }}
          className="block w-full text-xs text-gray-300 file:mr-3 file:rounded-lg file:border-0 file:bg-purple-900/60 file:px-3 file:py-2 file:text-xs file:font-semibold file:text-purple-200"
        />
      </div>
      <FormError message={error} />
      <div className="max-w-xs">
        <SubmitButton busy={busy} disabled={!file}>
          Import roster
        </SubmitButton>
      </div>

      {report?.imported && (
        <p
          role="status"
          className="rounded-xl border border-emerald-500/30 bg-emerald-950/30 p-3 text-xs text-emerald-200"
        >
          Imported {report.rows} rows: {report.created} new, {report.updated} updated,{' '}
          {report.unchanged} unchanged.
        </p>
      )}
      {report && !report.imported && (
        <div
          role="alert"
          className="space-y-2 rounded-xl border border-red-500/30 bg-red-950/40 p-3 text-xs text-red-300"
        >
          <p className="font-semibold">
            Nothing was imported. Fix these {report.errors.length} problem
            {report.errors.length === 1 ? '' : 's'} and upload again.
          </p>
          <table className="w-full text-left">
            <thead className="text-[10px] uppercase">
              <tr>
                <th scope="col" className="pr-3">
                  Line
                </th>
                <th scope="col" className="pr-3">
                  Field
                </th>
                <th scope="col">Problem</th>
              </tr>
            </thead>
            <tbody>
              {report.errors.map((row, index) => (
                <tr key={`${row.line}-${row.field}-${index}`}>
                  <td className="pr-3">{row.line}</td>
                  <td className="pr-3 font-mono">{row.field}</td>
                  <td>{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </form>
  )
}

function RosterSearch() {
  const [q, setQ] = useState('')
  const term = useDebounced(q.trim(), 250)
  const students = useQuery({
    queryKey: ['students', term],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/students', {
        params: { query: { q: term, limit: 20 } },
      })
      if (!data) throw new Error('students unavailable')
      return data
    },
  })
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-bold tracking-wider text-purple-400 uppercase">Roster</h3>
      <TextField
        label="Search by USN or name"
        icon="fa-solid fa-magnifying-glass"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        maxLength={100}
        placeholder="1AB21 or Asha"
      />
      {students.isError && <FormError message="Could not load the roster." />}
      {students.data && students.data.length === 0 && (
        <p className="text-xs text-gray-400">
          {term ? 'No student matches.' : 'The roster is empty. Upload a CSV first.'}
        </p>
      )}
      {students.data && students.data.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
            <tr>
              <th scope="col" className="pb-2 pr-4">
                USN
              </th>
              <th scope="col" className="pb-2 pr-4">
                Name
              </th>
              <th scope="col" className="pb-2">
                Class / section
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {students.data.map((student) => (
              <tr key={student.id}>
                <td className="py-2 pr-4 font-mono text-cyan-300">{student.usn}</td>
                <td className="py-2 pr-4 text-white">{student.name}</td>
                <td className="py-2 text-gray-300">{student.class_section}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function RosterSection() {
  return (
    <GlassPanel as="section" aria-labelledby="roster-title" className="space-y-6 p-6">
      <h2 id="roster-title" className="flex items-center gap-2 text-lg font-bold text-white">
        <i className="fa-solid fa-users text-cyan-400" aria-hidden="true" /> Student roster
      </h2>
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <RosterImport />
        <RosterSearch />
      </div>
    </GlassPanel>
  )
}

/** `/admin` (administrators only): teacher accounts and the roster. */
export default function AdminPage() {
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-user-shield"
        iconTone="text-amber-400"
        title="Administration"
        description="Invite teachers, manage their access, and keep the student roster up to date."
      />
      <TeachersSection />
      <RosterSection />
    </div>
  )
}
