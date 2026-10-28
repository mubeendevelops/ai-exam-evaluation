import { Link } from 'react-router'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError } from '../../components/public/fields'
import { Badge, EmptyState, GlassPanel } from '../../components/ui'
import { sectionTitle } from './controls'
import {
  SME_STATUS,
  UNIT_STATE,
  percent,
  useProgress,
  type SmeProgress,
  type UnitProgress,
} from './data'

function Bar({ label, done, total }: { label: string; done: number; total: number }) {
  const pct = percent(done, total)
  return (
    <div className="flex items-center gap-2">
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="h-2 w-24 overflow-hidden rounded-full bg-gray-800"
      >
        <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] font-semibold text-gray-300">{pct}%</span>
    </div>
  )
}

const th = 'pb-2 pr-4'

function SmeTable({ smes }: { smes: readonly SmeProgress[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Progress of each SME</caption>
        <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
          <tr>
            <th scope="col" className={th}>
              SME
            </th>
            <th scope="col" className={th}>
              Units owned
            </th>
            <th scope="col" className={th}>
              Booklets
            </th>
            <th scope="col" className={th}>
              Approved
            </th>
            <th scope="col" className={th}>
              Pending
            </th>
            <th scope="col" className={th}>
              Progress
            </th>
            <th scope="col" className={th}>
              Overdue units
            </th>
            <th scope="col" className="pb-2">
              Last activity
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800">
          {smes.map((sme) => {
            const status = SME_STATUS[sme.status]
            return (
              <tr key={sme.user_id}>
                <th
                  scope="row"
                  className="space-x-1 py-2.5 pr-4 text-left font-semibold text-white"
                >
                  <span>{sme.display_name}</span>
                  <Badge tone={status.tone}>{status.label}</Badge>
                  {sme.heavy_load && <Badge tone="amber">Heavy load</Badge>}
                </th>
                <td className="py-2.5 pr-4 text-gray-300">{sme.units_owned}</td>
                <td className="py-2.5 pr-4 text-gray-300">{sme.booklets}</td>
                <td className="py-2.5 pr-4 font-semibold text-emerald-400">{sme.approved}</td>
                <td className="py-2.5 pr-4 font-semibold text-amber-300">{sme.pending}</td>
                <td className="py-2.5 pr-4">
                  <Bar
                    label={`${sme.display_name}: approved`}
                    done={sme.approved}
                    total={sme.booklets}
                  />
                </td>
                <td
                  className={`py-2.5 pr-4 ${sme.overdue_units > 0 ? 'font-semibold text-amber-300' : 'text-gray-300'}`}
                >
                  {sme.overdue_units}
                </td>
                <td className="py-2.5 text-gray-300">
                  {sme.last_activity ? (
                    <time dateTime={sme.last_activity}>
                      {new Date(sme.last_activity).toLocaleString()}
                    </time>
                  ) : (
                    'No activity yet'
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

function UnitTable({ units }: { units: readonly UnitProgress[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Progress of each unit</caption>
        <thead className="text-[10px] tracking-wider text-gray-400 uppercase">
          <tr>
            <th scope="col" className={th}>
              Unit
            </th>
            <th scope="col" className={th}>
              Paper
            </th>
            <th scope="col" className={th}>
              SME
            </th>
            <th scope="col" className={th}>
              Due
            </th>
            <th scope="col" className={th}>
              State
            </th>
            <th scope="col" className="pb-2">
              Progress
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800">
          {units.map((unit) => {
            const state = UNIT_STATE[unit.state]
            return (
              <tr key={unit.unit_id}>
                <th scope="row" className="py-2.5 pr-4 text-left font-semibold text-white">
                  <Link to={`/admin/upload/units/${unit.unit_id}`} className="hover:underline">
                    {unit.code}
                  </Link>
                </th>
                <td className="py-2.5 pr-4 text-gray-300">
                  {unit.exam_code || unit.paper_title || 'Paper deleted'} · {unit.year}
                  {unit.subject_name && (
                    <span className="block text-[11px] text-gray-500">{unit.subject_name}</span>
                  )}
                </td>
                <td className="py-2.5 pr-4 text-gray-300">{unit.owner_name}</td>
                <td className="space-x-1 py-2.5 pr-4 text-gray-300">
                  <span>{unit.due_date}</span>
                  {unit.overdue && <Badge tone="amber">Overdue</Badge>}
                </td>
                <td className="py-2.5 pr-4">
                  <Badge tone={state.tone}>{state.label}</Badge>
                </td>
                <td className="py-2.5">
                  <div className="space-y-1">
                    <Bar
                      label={`${unit.code}: approved`}
                      done={unit.booklets.approved}
                      total={unit.booklets.total}
                    />
                    <span className="text-[11px] text-gray-500">
                      {unit.booklets.approved} of {unit.booklets.total} approved
                    </span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** SME Progress Reports (P29, D163, D167): counts per SME and per unit. No marks and no speed
 * metric; a transferred unit's progress belongs to its current owner. */
export function ProgressPage() {
  const progress = useProgress()
  const found = progress.data

  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-chart-line"
        iconTone="text-emerald-400"
        title="SME Progress Reports"
        description="How far each SME and each unit has got. Pending counts the booklets of open units that are not approved yet."
      />
      {progress.isPending && <p className="text-xs text-gray-400">Loading the report…</p>}
      {progress.isError && (
        <FormError message="Could not load the report. Reload the page to try again." />
      )}
      {found && found.smes.length === 0 && (
        <EmptyState icon="fa-solid fa-chart-line" title="No progress to report yet">
          Progress per SME and per unit appears here once SMEs are invited and units are assigned.
        </EmptyState>
      )}
      {found && found.smes.length > 0 && (
        <>
          <GlassPanel as="section" aria-labelledby="sme-progress-title" className="space-y-4 p-6">
            <h2 id="sme-progress-title" className={sectionTitle}>
              <i className="fa-solid fa-users text-purple-400" aria-hidden="true" /> By SME
            </h2>
            <SmeTable smes={found.smes} />
          </GlassPanel>
          <GlassPanel as="section" aria-labelledby="unit-progress-title" className="space-y-4 p-6">
            <h2 id="unit-progress-title" className={sectionTitle}>
              <i className="fa-solid fa-layer-group text-amber-400" aria-hidden="true" /> By unit
            </h2>
            {found.units.length === 0 ? (
              <p className="text-xs text-gray-400">No units yet.</p>
            ) : (
              <UnitTable units={found.units} />
            )}
          </GlassPanel>
        </>
      )}
    </div>
  )
}
