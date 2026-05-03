"use client";

import { useState } from "react";

interface Props {
  onAnalyse: (jobText: string) => void;
  busy: boolean;
  disabled?: boolean;
  disabledReason?: string;
}

export default function JobInput({ onAnalyse, busy, disabled, disabledReason }: Props) {
  const [text, setText] = useState("");

  const canSubmit = !busy && !disabled && text.trim().length > 20;

  return (
    <div className="space-y-3">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Paste the full job description here…"
        rows={10}
        className="block w-full resize-y rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
      />
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          {disabled && disabledReason
            ? disabledReason
            : `${text.length} characters · paste at least a couple of sentences`}
        </p>
        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => onAnalyse(text)}
          className="inline-flex items-center justify-center rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Analysing…" : "Analyse Job Match"}
        </button>
      </div>
    </div>
  );
}
