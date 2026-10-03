/**
 * Static previews behind the four feature cards (docs/MainLogin.html demo modals). Everything
 * here is sample data, not product output. The analytics tiles are a mock-up only: no analytics
 * feature is in scope, and their figures are placeholders too.
 */
import type { DemoId } from './landing'

export interface DemoMeta {
  category: string
  title: string
}

export const demoMeta: Record<DemoId, DemoMeta> = {
  repository: { category: 'Repository Cloud', title: 'Explore Repository Schema' },
  mapAnswers: { category: 'Map Answers Demo', title: 'Answer Key Mapping & Dynamic Rubrics' },
  aiEvaluation: { category: 'AI Evaluation Demo', title: 'Tri-Window AI Evaluation Workstation' },
  analytics: {
    category: 'View Analytics',
    title: 'Multi-Tier Assessment Performance Analytics',
  },
}

export const repositoryDemo = {
  intro:
    "Explore Tarn's standardized Question & Answer JSON/Relational Schema. Designed for adaptive testing, taxonomical tagging, and rapid auto-paper generation.",
  attributes: [
    { name: 'question_id', type: 'UUIDv4 string' },
    { name: 'blooms_taxonomy_level', type: 'Apply / Analyze / Evaluate' },
    { name: 'cognitive_complexity_score', type: '0.00 - 1.00 (Float)' },
    { name: 'rubric_key_phrases', type: 'Array<String>' },
    { name: 'acceptable_variants', type: 'Nested Schema Array' },
  ],
  json: `{
  "q_id": "Q-TARN-8092",
  "subject": "Data Structures & Algorithms",
  "topic": "Graph Theory",
  "max_marks": 10,
  "question_text": "Explain Dijkstra's Algorithm with time complexity.",
  "canonical_answer": {
    "core_concept": "Greedy single-source shortest path",
    "key_nodes": ["Priority Queue", "Edge relaxation", "Non-negative weights"],
    "time_complexity": "O((V + E) log V)"
  }
}`,
} as const

export const mapAnswersDemo = {
  heading: 'Answer Generation & Key Linking Pipeline',
  sub: 'Select standard source mode to link reference keys to exam questions.',
  searchLabel: 'AI Search & Auto-Synthesize Key Rubric',
  searchValue: 'Quantum Computing Qubit Entanglement standard grading rubric 2026',
  linked: 'IEEE Quantum Curriculum Spec 2025-26',
  linkedNote: '(Auto-extracted 4 key evaluation benchmarks).',
  uploadLabel: 'Direct Key Model Upload (.PDF / .DOCX / Key Schema)',
  uploadHint: 'Drag & Drop official answer key file or click to browse',
  uploadTypes: 'Supports PDF, JSON, DOCX up to 25MB',
} as const

export const aiEvaluationDemo = {
  candidate: 'STD-2026-8891',
  paper: 'Mathematics Paper II',
  scanLines: ['dx/dt = k(100 - x)', 'Integrating both sides:', '-ln(100 - x) = kt + C'],
  box: 'B-Box: [x:120, y:450, w:300, h:110]',
  ocrSteps: ['dx/dt = k(100 - x)', '∫ (1 / (100 - x)) dx = ∫ k dt', '-ln|100 - x| = kt + C'],
  confidence: 'Conf: 98.4%',
  breakdown: ['Differential Setup (+2)', 'Integration Steps (+3)'],
  suggested: 'Suggested: 5/5',
  maxMarks: 5,
} as const

export const analyticsDemo = {
  intro:
    'Real-time analytics engine displaying tiered insights across student, class, subject, and institutional metrics.',
  tiles: [
    {
      tier: 'Student Level',
      value: '88.4%',
      caption: 'Avg Candidate Score',
      detail: ['Student ID: STD-9021', 'Percentile: 94th'],
      tone: 'purple',
    },
    {
      tier: 'Class Level',
      value: '76.2%',
      caption: 'Class Section 12-A',
      detail: ['Enrolled: 42 Students', 'Median Score: 78%'],
      tone: 'cyan',
    },
    {
      tier: 'Subject Level',
      value: '82.0%',
      caption: 'Physics & Mechanics',
      detail: ['Toughness Index: Medium', 'Evaluated: 1,420 Papers'],
      tone: 'indigo',
    },
    {
      tier: 'Institute Level',
      value: '99.1%',
      caption: 'Overall SLA Accuracy',
      detail: ['Active Workspaces: 18', 'Total Scans Processed: 1.2M'],
      tone: 'emerald',
    },
  ],
  curveTitle: 'Institutional Performance Bell Curve',
  /** Bar heights (%) and colours of the mock-up. */
  curve: [
    { height: 20, cls: 'bg-purple-900/40' },
    { height: 45, cls: 'bg-purple-800/50' },
    { height: 85, cls: 'bg-cyan-600/60' },
    { height: 95, cls: 'bg-purple-600/70' },
    { height: 60, cls: 'bg-indigo-600/60' },
    { height: 30, cls: 'bg-emerald-600/40' },
  ],
} as const
