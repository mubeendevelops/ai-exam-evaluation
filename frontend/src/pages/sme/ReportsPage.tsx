import { PageBanner } from '../../components/app/PageBanner'
import { GlassPanel } from '../../components/ui'
import { useMyReport, type MyReport } from './data'

function days(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} days`
}

function Card({
  label,
  value,
  tone = 'text-white',
  children,
}: {
  label: string
  value: string
  tone?: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/60 p-4">
      <h3 className="text-xs text-gray-400">{label}</h3>
      <p className={`mt-1 text-2xl font-bold ${tone}`}>{value}</p>
      <div className="mt-1 text-[11px] text-gray-500">{children}</div>
    </div>
  )
}

function units(n: number): string {
  return `${n} unit${n === 1 ? '' : 's'}`
}

function Bar({ value, max, tone }: { value: number; max: number; tone: string }) {
  return (
    <div aria-hidden="true" className="h-2 w-full min-w-16 rounded-full bg-gray-800">
      <div
        className={`h-2 rounded-full ${tone}`}
        style={{ width: `${max > 0 ? (value / max) * 100 : 0}%` }}
      />
    </div>
  )
}

function Quarters({ report }: { report: MyReport }) {
  const rows = report.quarters
  const mostUnits = Math.max(0, ...rows.map((q) => q.units_submitted))
  const mostBooklets = Math.max(0, ...rows.map((q) => q.booklets_approved))
  return (
    <GlassPanel as="section" aria-labelledby="quarters-heading" className="space-y-4 p-6">
      <div>
        <h2 id="quarters-heading" className="text-base font-semibold text-white">
          Work per calendar quarter
        </h2>
        <p className="text-xs text-gray-400">
          The last four quarters, the current one last. A unit counts in the quarter you last
          submitted it; a booklet in the quarter its result sheet was first issued.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Units submitted and booklets approved per quarter</caption>
          <thead className="text-[11px] tracking-wider text-gray-400 uppercase">
            <tr>
              <th scope="col" className="px-3 py-2">
                Quarter
              </th>
              <th scope="col" className="px-3 py-2">
                Units submitted
              </th>
              <th scope="col" className="px-3 py-2">
                Booklets approved
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {rows.map((q, i) => (
              <tr key={q.label}>
                <th scope="row" className="px-3 py-2.5 font-semibold text-white">
                  {q.label}
                  {i === rows.length - 1 && (
                    <span className="ml-2 font-normal text-gray-500">(so far)</span>
                  )}
                </th>
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-3">
                    <span className="w-6 text-right font-semibold text-white">
                      {q.units_submitted}
                    </span>
                    <Bar value={q.units_submitted} max={mostUnits} tone="bg-amber-500" />
                  </div>
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-3">
                    <span className="w-8 text-right font-semibold text-white">
                      {q.booklets_approved}
                    </span>
                    <Bar value={q.booklets_approved} max={mostBooklets} tone="bg-emerald-500" />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </GlassPanel>
  )
}

/**
 * My Reports (P36, D226): the SME's own figures from `GET /me/reports`, counted over the units
 * they own now (credit follows the owner, D163). Turnaround, on-time rate, current load and the
 * last four calendar quarters. No accuracy score, no speed per hour, no marks (D167).
 */
export default function ReportsPage() {
  const query = useMyReport()
  const report = query.data
  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-file-lines"
        iconTone="text-indigo-400"
        title="My Reports"
        description="Your review turnaround, on-time rate and work per quarter."
      />
      {query.isError && (
        <p role="alert" className="text-xs text-red-300">
          Your reports could not be loaded. Reload the page to try again.
        </p>
      )}
      {query.isPending && <p className="text-xs text-gray-400">Loading your reports…</p>}
      {report && (
        <>
          <GlassPanel as="section" aria-labelledby="figures-heading" className="space-y-4 p-6">
            <div>
              <h2 id="figures-heading" className="text-base font-semibold text-white">
                My Operations &amp; Review Reports
              </h2>
              <p className="text-xs text-gray-400">
                Over the units assigned to you now; a unit moved to you counts from the day you
                received it.
              </p>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Card
                label="Average Turnaround"
                value={days(report.turnaround_lifetime.average_days)}
              >
                <span>
                  This academic year ({report.academic_year.label}):{' '}
                  <strong className="text-gray-300">
                    {days(report.turnaround_year.average_days)}
                  </strong>
                </span>
                <br />
                Over {units(report.turnaround_lifetime.units)} handed in
              </Card>
              <Card
                label="On-time Rate"
                value={report.on_time_rate === null ? '—' : `${report.on_time_rate.toFixed(1)}%`}
                tone="text-indigo-400"
              >
                {report.units_handed_in === 0
                  ? 'No unit handed in yet'
                  : `of ${units(report.units_handed_in)} submitted by their due date`}
              </Card>
              <Card
                label="Current Load"
                value={`${report.open_units} open`}
                tone="text-emerald-400"
              >
                {report.booklets_pending} booklet{report.booklets_pending === 1 ? '' : 's'} pending
                in your open units
              </Card>
            </div>
          </GlassPanel>
          <Quarters report={report} />
        </>
      )}
    </div>
  )
}
