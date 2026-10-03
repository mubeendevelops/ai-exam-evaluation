import type { components } from './schema'

export type User = components['schemas']['UserOut']

type Listener = () => void

/**
 * The access token lives in memory only (never localStorage): a reload gets a new one from the
 * refresh cookie. Components subscribe to learn when the session was lost.
 */
let accessToken: string | null = null
const lostListeners = new Set<Listener>()

export const session = {
  get token(): string | null {
    return accessToken
  },
  set(token: string): void {
    accessToken = token
  },
  clear(): void {
    accessToken = null
  },
  /** Called when a refresh was refused: the user has to sign in again. */
  onLost(listener: Listener): () => void {
    lostListeners.add(listener)
    return () => lostListeners.delete(listener)
  },
  notifyLost(): void {
    accessToken = null
    lostListeners.forEach((listener) => listener())
  },
}
