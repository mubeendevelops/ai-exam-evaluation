import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { NETWORK_PROBLEM, problemOf } from '../../api/errors'
import type { components } from '../../api/schema'
import { FormError, SubmitButton } from '../../components/public/fields'

type RosterReport = components['schemas']['RosterReportOut']

const MAX_BYTES = 1_000_000

/** The CSV import of the roster (P4), unchanged: upsert by USN, all or nothing. */
export function RosterImport() {
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
