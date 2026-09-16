// src/features/content/ContentUploadForm.tsx — paste text, preview the
// split, edit the candidates, then save.
//
// SPLIT AND SAVE ARE TWO CALLS, deliberately not collapsed — see
// api/schemas/content.py's module docstring. "Split" (POST /content/split)
// writes nothing at all; the candidates below live only in this component's
// state until "Save" (POST /content) is clicked. A teacher can merge two
// candidates, drop one, or type a whole new one before anything is
// persisted.
import { useState } from "react";

import { useCreateContent, useSplitContent, type CreateContentResponse } from "../../api/queries";

interface ContentUploadFormProps {
  onSaved?: (result: CreateContentResponse) => void;
}

export function ContentUploadForm({ onSaved }: ContentUploadFormProps) {
  const [sourceDocument, setSourceDocument] = useState("");
  const [text, setText] = useState("");
  const [candidates, setCandidates] = useState<string[] | null>(null);
  const [savedCount, setSavedCount] = useState<number | null>(null);

  const split = useSplitContent();
  const create = useCreateContent();

  function submitSplit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || !sourceDocument.trim()) return;
    setSavedCount(null);
    split.mutate(
      { text, source_document: sourceDocument.trim() },
      { onSuccess: (data) => setCandidates(data.candidates.map((c) => c.content)) },
    );
  }

  function updateCandidate(index: number, value: string) {
    setCandidates((prev) => (prev ? prev.map((c, i) => (i === index ? value : c)) : prev));
  }

  function removeCandidate(index: number) {
    setCandidates((prev) => (prev ? prev.filter((_, i) => i !== index) : prev));
  }

  function mergeIntoPrevious(index: number) {
    setCandidates((prev) => {
      if (!prev || index === 0) return prev;
      const merged = [...prev];
      merged[index - 1] = `${merged[index - 1]}\n\n${merged[index]}`;
      merged.splice(index, 1);
      return merged;
    });
  }

  function addBlankCandidate() {
    setCandidates((prev) => [...(prev ?? []), ""]);
  }

  function startOver() {
    setCandidates(null);
    setSavedCount(null);
  }

  function save() {
    const nonBlank = (candidates ?? []).map((c) => c.trim()).filter(Boolean);
    if (nonBlank.length === 0) return;
    create.mutate(
      { source_document: sourceDocument.trim(), paragraphs: nonBlank },
      {
        onSuccess: (data) => {
          setSavedCount(data.created);
          setCandidates(null);
          setText("");
          onSaved?.(data);
        },
      },
    );
  }

  const sentenceCounts = candidates?.map((c) => c.split(/(?<=[.!?])\s+/).filter(Boolean).length) ?? [];

  return (
    <details className="rounded-lg border border-slate-200 bg-white p-4" open>
      <summary className="cursor-pointer text-sm font-semibold text-slate-800">Upload content</summary>

      {!candidates && (
        <form onSubmit={submitSplit} className="mt-4 space-y-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">Source name</span>
            <input
              className="select"
              placeholder='e.g. "Unit 3 notes"'
              value={sourceDocument}
              onChange={(e) => setSourceDocument(e.target.value)}
            />
            <span className="text-xs text-slate-400">
              Every paragraph created from this text carries this name — it's how the picker's document filter
              groups your uploads.
            </span>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">Content</span>
            <textarea
              className="select"
              rows={10}
              placeholder="Paste your content here. Separate paragraphs with a blank line."
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>

          {split.error && <p className="text-sm text-red-600">{(split.error as Error).message}</p>}

          <button type="submit" className="btn-primary" disabled={!text.trim() || !sourceDocument.trim() || split.isPending}>
            {split.isPending ? "Splitting…" : "Split into paragraphs"}
          </button>

          {savedCount !== null && (
            <div className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
              Saved {savedCount} paragraph{savedCount === 1 ? "" : "s"} from {sourceDocument || "that upload"}. They're
              ready to pick from on the right.
            </div>
          )}
        </form>
      )}

      {candidates && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-600">
              {candidates.length} paragraph{candidates.length === 1 ? "" : "s"}, nothing saved yet — edit, merge, or
              remove before saving.
            </p>
            <button type="button" className="text-xs text-slate-500 hover:text-slate-800" onClick={startOver}>
              Start over
            </button>
          </div>

          {candidates.map((content, index) => (
            <div key={index} className="rounded-md border border-slate-200 p-3">
              <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
                <span>
                  Paragraph {index + 1} — {content.length} chars, {sentenceCounts[index] ?? 0} sentence
                  {sentenceCounts[index] === 1 ? "" : "s"}
                </span>
                <div className="flex gap-3">
                  {index > 0 && (
                    <button type="button" className="hover:text-slate-700" onClick={() => mergeIntoPrevious(index)}>
                      Merge into previous
                    </button>
                  )}
                  <button type="button" className="hover:text-red-600" onClick={() => removeCandidate(index)}>
                    Remove
                  </button>
                </div>
              </div>
              <textarea
                className="select w-full"
                rows={3}
                value={content}
                onChange={(e) => updateCandidate(index, e.target.value)}
              />
            </div>
          ))}

          <button type="button" className="btn-secondary" onClick={addBlankCandidate}>
            + Add a paragraph
          </button>

          {create.error && <p className="text-sm text-red-600">{(create.error as Error).message}</p>}

          <button
            type="button"
            className="btn-primary"
            disabled={candidates.every((c) => !c.trim()) || create.isPending}
            onClick={save}
          >
            {create.isPending ? "Saving…" : `Save ${candidates.filter((c) => c.trim()).length} paragraph(s)`}
          </button>
        </div>
      )}
    </details>
  );
}
