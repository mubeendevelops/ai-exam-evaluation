import { Link } from 'react-router'
import { brand } from '../../content/landing'

/** Sticky header of MainLogin.html: TARN KNOWLEDGE branding and the Register button. */
export function TopBar({ onRegister }: { onRegister: () => void }) {
  return (
    <header className="glass-box sticky top-0 z-40 border-b border-gray-800/80">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-20 items-center justify-between">
          <div className="flex items-center space-x-3.5">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-tr from-purple-600 via-indigo-600 to-cyan-400 text-2xl text-white shadow-lg shadow-purple-900/40">
              <i className="fa-solid fa-cloud-bolt" aria-hidden="true" />
            </div>
            <div>
              <Link
                to="/"
                className="group flex items-center gap-2"
                aria-label="Tarn Knowledge home"
              >
                <span className="bg-gradient-to-r from-white via-gray-100 to-purple-300 bg-clip-text text-xl font-black tracking-tight text-transparent transition-all duration-300 group-hover:to-cyan-400">
                  {brand.name}
                </span>
              </Link>
              <p className="text-[10px] font-bold tracking-widest text-cyan-400 uppercase">
                {brand.tagline}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-4">
            <a
              href={brand.site}
              target="_blank"
              rel="noopener noreferrer"
              className="hidden items-center gap-2 text-xs text-gray-400 transition hover:text-white sm:flex"
            >
              <i className="fa-solid fa-globe text-purple-400" aria-hidden="true" />
              <span>{brand.siteLabel}</span>
            </a>
            <button
              type="button"
              onClick={onRegister}
              className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 px-5 py-2.5 text-xs font-bold text-white shadow-lg shadow-purple-900/30 transition hover:from-purple-500 hover:to-indigo-500"
            >
              <i className="fa-solid fa-user-plus" aria-hidden="true" />
              <span>Register</span>
            </button>
          </div>
        </div>
      </div>
    </header>
  )
}
