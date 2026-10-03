import { useState } from 'react'
import {
  featuresSection,
  features,
  type DemoId,
  type FeatureCard,
  type FeatureTone,
} from '../../content/landing'
import { Badge } from '../ui'
import { DemoModal } from './DemoModal'

const toneStyles: Record<
  FeatureTone,
  { icon: string; eyebrow: string; title: string; cta: string }
> = {
  purple: {
    icon: 'bg-purple-950 border-purple-500/40 text-purple-400',
    eyebrow: 'text-purple-400',
    title: 'group-hover:text-purple-300',
    cta: 'text-purple-400',
  },
  cyan: {
    icon: 'bg-cyan-950 border-cyan-500/40 text-cyan-400',
    eyebrow: 'text-cyan-400',
    title: 'group-hover:text-cyan-300',
    cta: 'text-cyan-400',
  },
  indigo: {
    icon: 'bg-indigo-950 border-indigo-500/40 text-indigo-400',
    eyebrow: 'text-indigo-400',
    title: 'group-hover:text-indigo-300',
    cta: 'text-indigo-400',
  },
  emerald: {
    icon: 'bg-emerald-950 border-emerald-500/40 text-emerald-400',
    eyebrow: 'text-emerald-400',
    title: 'group-hover:text-emerald-300',
    cta: 'text-emerald-400',
  },
}

function Card({ card, onOpen }: { card: FeatureCard; onOpen: (demo: DemoId) => void }) {
  const tone = toneStyles[card.tone]
  return (
    <button
      type="button"
      onClick={() => onOpen(card.demo)}
      aria-haspopup="dialog"
      className="glass-box-interactive group relative flex flex-col justify-between space-y-4 overflow-hidden rounded-2xl p-6 text-left"
    >
      {card.previewOnly && (
        <Badge tone="amber" className="absolute top-4 right-4">
          Preview only
        </Badge>
      )}
      <div
        className={`flex h-12 w-12 items-center justify-center rounded-xl border text-xl transition duration-300 group-hover:scale-110 ${tone.icon}`}
      >
        <i className={card.icon} aria-hidden="true" />
      </div>
      <div className="space-y-2">
        <span className={`text-[10px] font-bold tracking-widest uppercase ${tone.eyebrow}`}>
          {card.eyebrow}
        </span>
        <h3 className={`text-lg font-bold text-white transition ${tone.title}`}>{card.title}</h3>
        <p className="text-xs leading-relaxed text-gray-400">{card.body}</p>
      </div>
      <div
        className={`flex items-center pt-2 text-xs font-semibold transition group-hover:translate-x-1 ${tone.cta}`}
      >
        <span>{card.cta}</span>
        <i className="fa-solid fa-chevron-right ml-1.5 text-[10px]" aria-hidden="true" />
      </div>
    </button>
  )
}

/** The four feature cards under the hero; each opens a static preview. */
export function FeatureCards() {
  const [demo, setDemo] = useState<DemoId | null>(null)
  return (
    <section aria-labelledby="features-title" className="space-y-8 pt-6">
      <div className="mx-auto max-w-2xl space-y-2 text-center">
        <h2 id="features-title" className="text-2xl font-extrabold text-white">
          {featuresSection.title}
        </h2>
        <p className="text-xs text-gray-400">{featuresSection.subtitle}</p>
      </div>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
        {features.map((card) => (
          <Card key={card.demo} card={card} onOpen={setDemo} />
        ))}
      </div>
      <DemoModal demo={demo} onClose={() => setDemo(null)} />
    </section>
  )
}
