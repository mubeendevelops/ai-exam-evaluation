import { useState } from 'react'
import { Outlet } from 'react-router'
import { PublicFooter } from './PublicFooter'
import { RegisterModal } from './RegisterModal'
import { TopBar } from './TopBar'

/**
 * Everything a visitor sees before signing in, in the tokens and font of MainLogin.html
 * (`.theme-public`): glow, top bar, page, footer, and the registration modal.
 */
export function PublicLayout() {
  const [registerOpen, setRegisterOpen] = useState(false)
  return (
    <div className="theme-public bg-grid-pattern flex min-h-screen flex-col justify-between selection:bg-purple-500 selection:text-white">
      <div
        aria-hidden="true"
        className="pointer-events-none fixed top-0 left-1/4 h-96 w-96 rounded-full bg-purple-600/15 blur-[120px]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed right-1/4 bottom-1/3 h-96 w-96 rounded-full bg-cyan-600/15 blur-[120px]"
      />
      <TopBar onRegister={() => setRegisterOpen(true)} />
      <main className="relative z-10 mx-auto w-full max-w-7xl grow px-4 py-10 sm:px-6 lg:px-8">
        <Outlet />
      </main>
      <PublicFooter />
      <RegisterModal open={registerOpen} onClose={() => setRegisterOpen(false)} />
    </div>
  )
}
