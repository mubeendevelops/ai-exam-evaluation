// src/features/questions/GenerateQuestionsForm.tsx — paragraph -> N draft
// questions, one LLM call, 201 with the ids back (not a job — see the
// schema's docstring on POST /questions/generate).
//
// Every question this produces lands at status='draft', hardcoded server
// side. There is no field here that could start it anywhere else — the
// mandatory review gate has no bypass.
import { useState } from "react";

import { useGenerateQuestions, type GenerateQuestionsRequest, type GeneratedQuestion } from "../../api/queries";

const STYLE_OPTIONS: NonNullable<GenerateQuestionsRequest["style"]>[] = ["long", "short", "one_word", "mcq"];

interface GenerateQuestionsFormProps {
  onGenerated?: (questions: GeneratedQuestion[]) => void;
}

export function GenerateQuestionsForm({ onGenerated }: GenerateQuestionsFormProps) {
  const [paragraphId, setParagraphId] = useState("");
  const [count, setCount] = useState(3);
  const [style, setStyle] = useState<NonNullable<GenerateQuestionsRequest["style"]> | "">("");
  const [intentHint, setIntentHint] = useState("");
  const [lastResult, setLastResult] = useState<GeneratedQuestion[] | null>(null);

  const generate = useGenerateQuestions();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!paragraphId.trim()) return;
    generate.mutate(
      {
        paragraph_id: paragraphId.trim(),
        count,
        style: style || null,
        intent_hint: intentHint.trim() || null,
        stub_llm: false,
      },
      {
        onSuccess: (data) => {
          setLastResult(data.questions);
          onGenerated?.(data.questions);
        },
      },
    );
  }

  return (
    <details className="rounded-lg border border-slate-200 bg-white p-4" open>
      <summary className="cursor-pointer text-sm font-semibold text-slate-800">Generate questions</summary>

      <form onSubmit={submit} className="mt-4 space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm sm:col-span-2">
            <span className="font-medium text-slate-700">Paragraph ID</span>
            <input
              className="select"
              placeholder="UUID of an already-uploaded, active paragraph"
              value={paragraphId}
              onChange={(e) => setParagraphId(e.target.value)}
            />
            <span className="text-xs text-slate-400">
              There's no paragraph picker yet — this API has no endpoint to list uploaded content, so paste the id.
            </span>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">How many</span>
            <input
              type="number"
              min={1}
              max={20}
              className="select"
              value={count}
              onChange={(e) => setCount(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
            />
            <span className="text-xs text-slate-400">Capped at 20 — each one is a draft a human then reads.</span>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">Style</span>
            <select className="select" value={style} onChange={(e) => setStyle(e.target.value as typeof style)}>
              <option value="">Model chooses per question</option>
              {STYLE_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm sm:col-span-2">
            <span className="font-medium text-slate-700">Intent hint (optional)</span>
            <input
              className="select"
              placeholder='e.g. "focus on definitions, easy difficulty"'
              value={intentHint}
              onChange={(e) => setIntentHint(e.target.value)}
            />
          </label>
        </div>

        {generate.error && <p className="text-sm text-red-600">{(generate.error as Error).message}</p>}

        <button type="submit" className="btn-primary" disabled={!paragraphId.trim() || generate.isPending}>
          {generate.isPending ? "Generating…" : "Generate drafts"}
        </button>
      </form>

      {lastResult && (
        <div className="mt-4 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
          Generated {lastResult.length} question{lastResult.length === 1 ? "" : "s"}, all at{" "}
          <span className="font-semibold">draft</span>. None is usable in a paper until it passes both gates below —
          filter by "Draft" to find them.
        </div>
      )}
    </details>
  );
}
