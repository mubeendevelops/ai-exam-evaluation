import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In Docker Compose the API is reachable as http://api:8000 (VITE_API_PROXY_TARGET);
// on the host it is on localhost.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: { '/api': apiTarget },
  },
  test: {
    environment: 'jsdom',
    // The suites are heavy for a busy laptop: a first render can pass the 5 s default.
    testTimeout: 15_000,
    setupFiles: ['./src/test-setup.ts'],
    // Playwright specs live in e2e/ and run with `npm run test:e2e`.
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
