import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { PageBanner } from '../../components/app/PageBanner'
import { FormError } from '../../components/public/fields'
import { Badge, GlassPanel, type Tone } from '../../components/ui'
import { useHealth } from '../../hooks/useHealth'
import { sectionTitle } from './controls'
import {
  formatBytes,
  useDashboard,
  useSystemFigures,
  type Dashboard,
  type StageCounts,
} from './data'

/** What an audit action reads as in the feed: "<who> <this> <unit>". */
const ACTIVITY: Record<string, string> = {
  'unit.created': 'created unit',
  'unit.uploaded': 'uploaded booklets to',
  'unit.transferred': 'transferred',
  'unit.submitted': 'submitted',
  'unit.returned': 'returned',
  'unit.frozen': 'froze',
  'booklet.approved': 'approved a booklet of',
  'booklet.replaced': 'replaced a booklet of',
  'delegation.requested': 'asked to reassign',
  'delegation.declined': 'declined to reassign',
  'delegation.withdrawn': 'withdrew the reassignment request for',
  'account.created': 'invited an SME',
  'account.disabled': 'suspended an SME',
  'account.enabled': 'resumed an SME',
  'account.removed': 'removed an SME',
  'subject.granted': 'granted a subject',
  'subject.revoked': 'revoked a subject',
  'roster.imported': 'imported the student roster',
}

export function describeActivity(item: Dashboard['activity'][number]): string {
  const who = item.actor_name ?? 'Tarn'
  const what = ACTIVITY[item.action] ?? item.action
  return item.unit_code ? `${who} ${what} ${item.unit_code}` : `${who} ${what}`
}

function Figure({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold tracking-wider text-gray-400 uppercase">{label}</dt>
      <dd className={`mt-1 text-2xl font-bold ${tone ?? 'text-white'}`}>{value}</dd>
    </div>
  )
}

function Panel({
  id,
  icon,
  iconTone,
  title,
  children,
}: {
  id: string
  icon: string
  iconTone: string
  title: string
  children: ReactNode
}) {
  return (
    <GlassPanel as="section" aria-labelledby={id} className="space-y-4 p-6">
      <h2 id={id} className={sectionTitle}>
        <i className={`${icon} ${iconTone}`} aria-hidden="true" /> {title}
      </h2>
      {children}
    </GlassPanel>
  )
}

const STAGES: { key: keyof Omit<StageCounts, 'total'>; label: string; bar: string }[] = [
  { key: 'processing', label: 'Processing', bar: 'bg-gray-500' },
  { key: 'ready', label: 'Ready to mark', bar: 'bg-cyan-500' },
  { key: 'in_review', label: 'In review', bar: 'bg-amber-500' },
  { key: 'approved', label: 'Approved', bar: 'bg-emerald-500' },
]

function BookletStages({ stages }: { stages: StageCounts }) {
  return (
    <>
      {stages.total > 0 && (
        <div
          role="img"
          aria-label={`${stages.total} booklets by stage`}
          className="flex h-2.5 w-full overflow-hidden rounded-full bg-gray-800"
        >
          {[...STAGES, { key: 'attention' as const, label: '', bar: 'bg-red-500' }].map((s) => (
            <div
              key={s.key}
              className={s.bar}
              style={{ width: `${(stages[s.key] / stages.total) * 100}%` }}
            />
          ))}
        </div>
      )}
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {STAGES.map((s) => (
          <Figure key={s.key} label={s.label} value={stages[s.key]} />
        ))}
      </dl>
      {stages.attention > 0 && (
        <p className="text-xs text-red-300">
          {stages.attention} booklet{stages.attention === 1 ? '' : 's'} need a retake or failed: see
          the unit pages in Upload & Assign Papers.
        </p>
      )}
      {stages.total === 0 && <p className="text-xs text-gray-400">No booklets uploaded yet.</p>}
    </>
  )
}

function toneOf(state: 'up' | 'down' | 'unknown'): Tone {
  return state === 'up' ? 'emerald' : state === 'down' ? 'red' : 'gray'
}

/** System status and what the college stores. No limits until the Provisioner (D167). */
function HealthBox() {
  const health = useHealth()
  const system = useSystemFigures()
  const api =
    health.phase === 'up'
      ? { state: 'up' as const, text: `Running (version ${health.health.version})` }
      : health.phase === 'down'
        ? { state: 'down' as const, text: 'Not reachable' }
        : { state: 'unknown' as const, text: 'Checking…' }
  const worker =
    health.phase === 'up'
      ? {
          state: health.health.worker.status,
          text:
            health.health.worker.status === 'up'
              ? 'Running'
              : health.health.worker.status === 'down'
                ? 'Not reachable'
                : 'Not configured',
        }
      : { state: 'unknown' as const, text: health.phase === 'down' ? 'Unknown' : 'Checking…' }

  return (
    <Panel
      id="health-title"
      icon="fa-solid fa-heart-pulse"
      iconTone="text-emerald-400"
      title="System health"
    >
      <dl className="space-y-3 text-xs">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-gray-400">API</dt>
          <dd>
            <Badge tone={toneOf(api.state)}>{api.text}</Badge>
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-gray-400">Worker</dt>
          <dd>
            <Badge tone={toneOf(worker.state)}>{worker.text}</Badge>
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-gray-400">Queue</dt>
          <dd className="font-semibold text-white">
            {system.data
              ? `${system.data.queue_queued} waiting · ${system.data.queue_running} running`
              : system.isError
                ? 'Unavailable'
                : '…'}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-gray-400">Storage used</dt>
          <dd className="font-semibold text-white">
            {system.data
              ? formatBytes(system.data.storage_bytes)
              : system.isError
                ? 'Unavailable'
                : '…'}
          </dd>
        </div>
      </dl>
      <p className="text-[11px] text-gray-500">No storage limit is set yet.</p>
    </Panel>
  )
}

/** Dashboard Overview (P29, D167): counts of SMEs, units and booklets; never marks. */
export function OverviewPage() {
  const dashboard = useDashboard()
  const found = dashboard.data

  return (
    <div className="space-y-6">
      <PageBanner
        icon="fa-solid fa-gauge-high"
        iconTone="text-purple-400"
        title="Dashboard Overview"
        description="How the college's evaluation is going, at a glance."
      />
      {dashboard.isPending && <p className="text-xs text-gray-400">Loading the figures…</p>}
      {dashboard.isError && (
        <FormError message="Could not load the figures. Reload the page to try again." />
      )}
      {found && (
        <>
          <div className="grid gap-6 md:grid-cols-2">
            <Panel id="smes-title" icon="fa-solid fa-users" iconTone="text-purple-400" title="SMEs">
              <dl className="grid grid-cols-3 gap-4">
                <Figure label="Active" value={found.smes.active} tone="text-emerald-400" />
                <Figure label="Idle" value={found.smes.idle} tone="text-cyan-300" />
                <Figure label="Suspended" value={found.smes.suspended} tone="text-red-400" />
              </dl>
              <p className="text-[11px] text-gray-500">Idle: active, with no open unit.</p>
            </Panel>
            <Panel
              id="units-title"
              icon="fa-solid fa-layer-group"
              iconTone="text-amber-400"
              title="Units"
            >
              <dl className="grid grid-cols-4 gap-4">
                <Figure label="Open" value={found.units.open} />
                <Figure label="Submitted" value={found.units.submitted} />
                <Figure label="Frozen" value={found.units.frozen} />
                <Figure
                  label="Overdue"
                  value={found.units.overdue}
                  tone={found.units.overdue > 0 ? 'text-amber-300' : undefined}
                />
              </dl>
              <p className="text-[11px] text-gray-500">
                Overdue: open units past their due date.{' '}
                <Link to="/admin/progress" className="text-purple-300 hover:underline">
                  View detailed reports →
                </Link>
              </p>
            </Panel>
          </div>
          <Panel
            id="stages-title"
            icon="fa-solid fa-file-lines"
            iconTone="text-cyan-400"
            title="Booklets by stage"
          >
            <BookletStages stages={found.booklets} />
          </Panel>
        </>
      )}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Panel
            id="activity-title"
            icon="fa-solid fa-clock-rotate-left"
            iconTone="text-purple-400"
            title="Recent activity"
          >
            {found && found.activity.length === 0 && (
              <p className="text-xs text-gray-400">
                Nothing yet. Units, transfers and SME changes appear here as they happen.
              </p>
            )}
            {found && found.activity.length > 0 && (
              <ul className="space-y-2.5">
                {found.activity.map((item, i) => (
                  <li key={`${item.at}-${i}`} className="flex items-start gap-2 text-xs">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-purple-400" />
                    <span className="grow text-gray-200">{describeActivity(item)}</span>
                    <time dateTime={item.at} className="shrink-0 text-gray-500">
                      {new Date(item.at).toLocaleString()}
                    </time>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        <HealthBox />
      </div>
    </div>
  )
}
