import { brand, company } from '../../content/landing'

/** Footer of MainLogin.html with the registered address and contacts of Tarn Knowledge Services. */
export function PublicFooter() {
  return (
    <footer className="mt-16 border-t border-gray-800/80 bg-gray-950/90 text-xs text-gray-400 backdrop-blur-md">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        <div className="grid grid-cols-1 items-start gap-8 md:grid-cols-12">
          <div className="space-y-3 md:col-span-5">
            <div className="flex items-center space-x-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-tr from-purple-600 to-cyan-400 text-sm font-bold text-white">
                <i className="fa-solid fa-brain" aria-hidden="true" />
              </div>
              <span className="text-base font-extrabold tracking-tight text-white">
                {company.name}
              </span>
            </div>
            <p className="max-w-sm text-xs leading-relaxed text-gray-400">{company.about}</p>
          </div>

          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 md:col-span-7">
            <div className="space-y-1.5">
              <h3 className="flex items-center gap-1.5 text-xs font-bold tracking-wider text-purple-400 uppercase">
                <i className="fa-solid fa-location-dot" aria-hidden="true" /> Corporate Headquarters
              </h3>
              <address className="text-xs leading-relaxed text-gray-300 not-italic">
                {company.addressLines.map((line, index) => (
                  <span key={line}>
                    {index > 0 && <br />}
                    {line}
                  </span>
                ))}
              </address>
            </div>

            <div className="space-y-1.5">
              <h3 className="flex items-center gap-1.5 text-xs font-bold tracking-wider text-cyan-400 uppercase">
                <i className="fa-solid fa-headset" aria-hidden="true" /> Direct Coordinates
              </h3>
              <p className="text-xs text-gray-300">
                <strong>Phone:</strong> {company.phone}
              </p>
              <p className="text-xs text-gray-300">
                <strong>Email:</strong>{' '}
                <a href={`mailto:${company.email}`} className="text-purple-400 hover:underline">
                  {company.email}
                </a>
              </p>
              <p className="text-xs text-gray-300">
                <strong>Official Portal:</strong>{' '}
                <a
                  href={brand.site}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-semibold text-cyan-400 hover:underline"
                >
                  {brand.site}
                </a>
              </p>
            </div>
          </div>
        </div>

        <div className="mt-8 flex flex-col items-center justify-between gap-4 border-t border-gray-800/60 pt-6 text-[11px] text-gray-500 sm:flex-row">
          <p>{company.copyright}</p>
          <ul className="flex space-x-6">
            {company.legal.map((item) => (
              <li key={item} title="Coming soon">
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </footer>
  )
}
