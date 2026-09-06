// src/features/papers/PaperGenerateForm.tsx — pattern -> paper, filled with
// live questions only (POST /papers/generate). There is no question-picker
// here: the bank fills every leaf slot itself by style+marks matching
// (core/paper_generator.py); this form only names the pattern and the paper.
import { useState } from "react";

import { useGeneratePaper, type PaperGenerateRequest, type PaperGenerateResponse } from "../../api/queries";

interface PaperGenerateFormProps {
  onGenerated: (response: PaperGenerateResponse) => void;
}

export function PaperGenerateForm({ onGenerated }: PaperGenerateFormProps) {
  const [patternId, setPatternId] = useState("");
  const [name, setName] = useState("");
  const [chooseCount, setChooseCount] = useState(1);
  const [marksTolerance, setMarksTolerance] = useState(1);

  const generate = useGeneratePaper();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!patternId.trim() || !name.trim()) return;
    const body: PaperGenerateRequest = {
      pattern_id: patternId.trim(),
      name: name.trim(),
      choose_count: chooseCount,
      marks_tolerance: marksTolerance,
    };
    generate.mutate(body, { onSuccess: onGenerated });
  }

  return (
    <form onSubmit={submit} className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-800">Generate a paper</h2>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="font-medium text-slate-700">Pattern ID</span>
          <input
            className="select"
            placeholder="UUID of an active paper_pattern"
            value={patternId}
            onChange={(e) => setPatternId(e.target.value)}
          />
          <span className="text-xs text-slate-400">
            No pattern picker yet — this API has no endpoint to list patterns, so paste the id.
          </span>
        </label>

        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="font-medium text-slate-700">Paper name</span>
          <input
            className="select"
            placeholder='e.g. "AIML CIA-2 Aug 2026"'
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700">Choose-count (optional sections)</span>
          <input
            type="number"
            min={1}
            className="select"
            value={chooseCount}
            onChange={(e) => setChooseCount(Math.max(1, Number(e.target.value) || 1))}
          />
          <span className="text-xs text-slate-400">Mandatory sections ignore this and always use every slot.</span>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700">Marks tolerance</span>
          <input
            type="number"
            min={0}
            step="0.5"
            className="select"
            value={marksTolerance}
            onChange={(e) => setMarksTolerance(Math.max(0, Number(e.target.value) || 0))}
          />
          <span className="text-xs text-slate-400">0 disables fuzzy matching — exact marks or no match.</span>
        </label>
      </div>

      {generate.error && <p className="mt-3 text-sm text-red-600">{(generate.error as Error).message}</p>}

      <button type="submit" className="btn-primary mt-3" disabled={!patternId.trim() || !name.trim() || generate.isPending}>
        {generate.isPending ? "Generating…" : "Generate paper"}
      </button>
    </form>
  );
}
