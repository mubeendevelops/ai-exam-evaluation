// src/features/questions/GenerateQuestionsForm.tsx — paragraph -> N draft
// questions, one LLM call, 201 with the ids back (not a job — see the
// schema's docstring on POST /questions/generate).
//
// Every question this produces lands at status='draft', hardcoded server
// side. There is no field here that could start it anywhere else — the
// mandatory review gate has no bypass.
//
// The paragraph field used to be a UUID pasted by hand — there was no
// endpoint to list uploaded content at all. It now opens the real picker
// (fixedStatus="active": generating from a superseded paragraph is refused
// server-side, so there is no reason to offer one here) and can also arrive
// pre-filled via `initialParagraphId`, which QuestionBank.tsx reads from a
// `?paragraph_id=` search param — the link the Content screen's "Generate
// questions" button lands on.
import { useState } from "react";

import {
  useGenerateQuestions,
  useParagraph,
  type GenerateQuestionsRequest,
  type GeneratedQuestion,
} from "../../api/queries";
import { ParagraphPicker } from "../content/ParagraphPicker";

const STYLE_OPTIONS: NonNullable<GenerateQuestionsRequest["style"]>[] = ["long", "short", "one_word", "mcq"];

interface GenerateQuestionsFormProps {
  onGenerated?: (questions: GeneratedQuestion[]) => void;
  initialParagraphId?: string;
}

export function GenerateQuestionsForm({ onGenerated, initialParagraphId }: GenerateQuestionsFormProps) {
  const [paragraphId, setParagraphId] = useState(initialParagraphId ?? "");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [count, setCount] = useState(3);
  const [style, setStyle] = useState<NonNullable<GenerateQuestionsRequest["style"]> | "">("");
  const [intentHint, setIntentHint] = useState("");
  const [lastResult, setLastResult] = useState<GeneratedQuestion[] | null>(null);

  const paragraph = useParagraph(paragraphId || undefined);
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
        <div className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700">Content to generate from</span>
          {paragraphId ? (
            <div className="flex items-start justify-between gap-2 rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
              <p className="line-clamp-2 text-sm text-slate-700">
                {paragraph.isLoading ? "Loading…" : paragraph.data?.content ?? paragraphId}
              </p>
              <div className="flex flex-shrink-0 gap-3 text-xs">
                <button type="button" className="text-slate-500 hover:text-slate-800" onClick={() => setPickerOpen((o) => !o)}>
                  Change
                </button>
                <button type="button" className="text-slate-500 hover:text-red-600" onClick={() => setParagraphId("")}>
                  Clear
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="btn-secondary self-start"
              onClick={() => setPickerOpen(true)}
            >
              Choose content…
            </button>
          )}

          {pickerOpen && (
            <div className="mt-2 rounded-md border border-slate-200 p-3">
              <ParagraphPicker
                fixedStatus="active"
                selectedId={paragraphId || undefined}
                onSelect={(p) => {
                  setParagraphId(p.paragraph_id);
                  setPickerOpen(false);
                }}
                pageSize={10}
              />
            </div>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
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
