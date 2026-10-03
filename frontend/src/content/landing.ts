/**
 * Copy of the public landing page (docs/MainLogin.html), kept out of the components so it can be
 * changed without touching them.
 *
 * `placeholder: true` marks figures and claims that are NOT verified: they come from the
 * reference page and stay until Tarn Knowledge Services confirms or replaces them. CLAUDE.md
 * lists them ("Placeholders"); `placeholderTexts()` is what the test checks that list against.
 */

export type ChipTone = 'purple' | 'cyan' | 'emerald'
export type FeatureTone = 'purple' | 'cyan' | 'indigo' | 'emerald'
export type DemoId = 'repository' | 'mapAnswers' | 'aiEvaluation' | 'analytics'

export interface Chip {
  icon: string
  tone: ChipTone
  text: string
  placeholder?: boolean
}

export interface FeatureCard {
  demo: DemoId
  icon: string
  tone: FeatureTone
  eyebrow: string
  title: string
  body: string
  cta: string
  /** Shown as a preview only: the feature is not in the product's scope yet. */
  previewOnly?: boolean
  /** The body text holds an unverified figure. */
  placeholder?: boolean
}

export const brand = {
  name: 'TARN KNOWLEDGE',
  tagline: 'Exam Evaluation Cloud',
  site: 'https://tarnknowledge.com',
  siteLabel: 'tarnknowledge.com',
} as const

const chips: readonly Chip[] = [
  {
    icon: 'fa-solid fa-shield-halved',
    tone: 'purple',
    text: 'ISO Secure Cloud',
    placeholder: true,
  },
  { icon: 'fa-solid fa-bolt', tone: 'cyan', text: '99.9% Evaluation Accuracy', placeholder: true },
  { icon: 'fa-solid fa-chart-line', tone: 'emerald', text: 'Real-Time Analytics' },
]

export const hero = {
  badge: 'Next-Gen Enterprise Assessment Infrastructure',
  titleLead: 'AI-Driven Evaluation &',
  titleAccent: 'Question Cloud',
  body: "Elevate academic standardizations with Tarn Knowledge Services' next-generation Evaluation Cloud. Streamline question bank generation, automated key mappings, and high-precision evaluation pipelines.",
  chips,
}

export const featuresSection = {
  title: 'Powerful Cloud Assessment Modules',
  subtitle: "Discover Tarn's intelligent suite designed for modern academic evaluation.",
} as const

export const features: readonly FeatureCard[] = [
  {
    demo: 'repository',
    icon: 'fa-solid fa-database',
    tone: 'purple',
    eyebrow: 'Repository Cloud',
    title: 'Get Cloud Question Bank',
    body: 'Access over 100,000+ pre-validated, mark-categorized questions with dynamic blueprint schemas and instant paper drafting.',
    cta: 'Explore Repository',
    placeholder: true,
  },
  {
    demo: 'mapAnswers',
    icon: 'fa-solid fa-diagram-project',
    tone: 'cyan',
    eyebrow: 'Precision Mapping',
    title: 'Cloud Map Answer Keys',
    body: 'Map custom answer variants, key phrases, and step-by-step rubrics directly to auto-segment handwritten answer pages.',
    cta: 'Map Answers demo',
  },
  {
    demo: 'aiEvaluation',
    icon: 'fa-solid fa-brain',
    tone: 'indigo',
    eyebrow: 'AI Evaluation Engine',
    title: 'Intelligent Assessment Assistant',
    body: 'Automate scan evaluation with high-accuracy OCR, semantic grading suggestions, and evaluator override panels.',
    cta: 'AI evaluation demo',
  },
  {
    demo: 'analytics',
    icon: 'fa-solid fa-chart-pie',
    tone: 'emerald',
    eyebrow: 'Institutional Insights',
    title: 'Cloud Analytics On House',
    body: 'Receive complimentary high-level performance metrics, difficulty heatmaps, and score distribution reports for all tests.',
    cta: 'View Analytics',
    previewOnly: true,
  },
]

export const company = {
  name: 'TARN KNOWLEDGE SERVICES',
  about:
    'Building intelligent enterprise architecture, educational platforms, AI tools, and specialized consulting ecosystems.',
  addressLines: [
    'A404, Meenakshi Mangalam, 2nd Main Road,',
    'Arekere, Bannerghatta Road, Hulimavu,',
    'Bangalore South, Bangalore – 560076,',
    'Karnataka, India',
  ],
  phone: '+91 9380163148',
  email: 'info@tarnknowledge.com',
  copyright: '© 2026 Tarn Knowledge Services. All rights reserved.',
  /** Pages that do not exist yet: shown as plain text, not links. */
  legal: ['Privacy Policy', 'Terms of Service', 'Security Spec'],
} as const

/** Every unverified claim on the landing page, in the words shown to the visitor. */
export function placeholderTexts(): string[] {
  return [
    ...hero.chips.filter((c) => c.placeholder).map((c) => c.text),
    ...features.filter((f) => f.placeholder).map((f) => f.body),
  ]
}
