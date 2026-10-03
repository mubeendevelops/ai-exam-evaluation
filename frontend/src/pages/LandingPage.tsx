import { hero } from '../content/landing'
import { FeatureCards } from '../components/public/FeatureCards'
import { SignInCard } from '../components/public/SignInCard'

const chipTone = {
  purple: 'text-purple-400',
  cyan: 'text-cyan-400',
  emerald: 'text-emerald-400',
} as const

/** `/`: hero with value chips, the sign-in card and the four feature cards. */
export default function LandingPage() {
  return (
    <div className="space-y-16">
      <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-7">
          <div className="inline-flex items-center gap-2 rounded-full border border-purple-500/40 bg-purple-950/80 px-3.5 py-1.5 text-xs font-semibold text-purple-300">
            <span className="h-2 w-2 animate-ping rounded-full bg-cyan-400" aria-hidden="true" />
            <span>{hero.badge}</span>
          </div>

          <h1 className="text-4xl leading-tight font-black text-white sm:text-5xl sm:leading-none lg:text-6xl lg:leading-none">
            {hero.titleLead} <br />
            <span className="bg-gradient-to-r from-purple-400 via-cyan-400 to-indigo-300 bg-clip-text text-transparent">
              {hero.titleAccent}
            </span>
          </h1>

          <p className="max-w-xl text-sm leading-relaxed text-gray-300 sm:text-base">{hero.body}</p>

          <ul className="grid grid-cols-2 gap-3 pt-2 sm:grid-cols-3">
            {hero.chips.map((chip, index) => (
              <li
                key={chip.text}
                className={`glass-box flex items-center gap-2.5 rounded-xl border border-gray-800 p-3 ${index === 2 ? 'col-span-2 sm:col-span-1' : ''}`}
              >
                <i className={`${chip.icon} text-base ${chipTone[chip.tone]}`} aria-hidden="true" />
                <span className="text-xs font-semibold text-gray-200">{chip.text}</span>
              </li>
            ))}
          </ul>
        </div>

        <SignInCard />
      </div>

      <FeatureCards />
    </div>
  )
}
