import { brand } from '../../content/landing'

/** Footer of project_idea.html. */
export function AppFooter() {
  return (
    <footer className="mt-12 border-t border-gray-800/80 bg-gray-950/80 text-xs text-gray-400 backdrop-blur-md">
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="flex flex-col items-center justify-between gap-4 text-[11px] sm:flex-row">
          <div className="flex items-center space-x-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-gradient-to-tr from-purple-600 to-cyan-400 text-xs font-bold text-white">
              <i className="fa-solid fa-brain" aria-hidden="true" />
            </div>
            <span className="text-sm font-extrabold tracking-tight text-white">
              TARN KNOWLEDGE SERVICES
            </span>
          </div>
          <p>© 2026 Tarn Knowledge Services Private Limited. All rights reserved.</p>
          <a
            href={brand.site}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold text-purple-400 hover:text-purple-300"
          >
            {brand.site}
          </a>
        </div>
      </div>
    </footer>
  )
}
