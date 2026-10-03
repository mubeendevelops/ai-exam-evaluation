import { useHealth } from '../../hooks/useHealth'
import { Pill, type Tone } from '../ui'

/** Replaces the prototype's fixed "AI Engine Online": real API and worker health. */
export function StatusPill() {
  const state = useHealth()
  let tone: Tone
  let label: string
  let title: string
  if (state.phase === 'loading') {
    tone = 'gray'
    label = 'Checking…'
    title = 'Checking the API and the worker'
  } else if (state.phase === 'down') {
    tone = 'red'
    label = 'API offline'
    title = 'The API does not answer'
  } else {
    const { health } = state
    const device = `${health.device.kind.toUpperCase()}: ${health.device.detail}`
    const details = `API v${health.version} (${health.environment}) · ${health.worker.detail} · ${device}`
    if (health.worker.status === 'up') {
      tone = 'emerald'
      label = 'API & worker online'
    } else if (health.worker.status === 'down') {
      tone = 'amber'
      label = 'Worker offline'
    } else {
      tone = 'cyan'
      label = 'API online'
    }
    title = details
  }
  return (
    <Pill tone={tone} pulse={tone === 'emerald'} title={title} className="hidden sm:flex">
      {label}
    </Pill>
  )
}
