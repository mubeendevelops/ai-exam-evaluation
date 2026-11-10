import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Session storage (a paper's unsaved draft, P35) never leaks from one test into the next.
afterEach(() => {
  cleanup()
  globalThis.sessionStorage?.clear()
})
