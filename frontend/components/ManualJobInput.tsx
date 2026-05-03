"use client";

import { useState } from "react";
import BatchCsvUpload from "@/components/BatchCsvUpload";
import JobFileInput from "@/components/JobFileInput";
import JobInput from "@/components/JobInput";
import JobUrlInput from "@/components/JobUrlInput";
import type { JobParsed } from "@/lib/types";

interface Props {
  onAnalyse: (jobText: string) => void;
  onError: (message: string) => void;
  busy: boolean;
  disabled?: boolean;
  disabledReason?: string;
}

type Tab = "text" | "url" | "file" | "csv";

const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: "text", label: "Paste text", hint: "Paste a JD directly into the textarea." },
  { key: "url", label: "From URL", hint: "Public job-posting URL (robots.txt-aware)." },
  { key: "file", label: "From file", hint: "Upload a PDF, DOCX, or TXT JD." },
  { key: "csv", label: "Bulk CSV", hint: "100-row cap. Returns the best CV per row." },
];

export default function ManualJobInput({
  onAnalyse,
  onError,
  busy,
  disabled,
  disabledReason,
}: Props) {
  const [active, setActive] = useState<Tab>("text");

  // URL / file paths feed extracted text into the same matcher used by the
  // textarea — keeps the downstream UX identical regardless of input source.
  function handleExtracted(text: string, _parsed: JobParsed) {
    onAnalyse(text);
  }

  return (
    <div className="space-y-3">
      <div role="tablist" className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1">
        {TABS.map((t) => {
          const isActive = t.key === active;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setActive(t.key)}
              className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition ${
                isActive
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content. Only the active tab is mounted so child state
          (e.g. JobInput's textarea draft) doesn't leak across tabs. */}
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        {active === "text" && (
          <JobInput
            onAnalyse={onAnalyse}
            busy={busy}
            disabled={disabled}
            disabledReason={disabledReason}
          />
        )}

        {active === "url" && (
          <JobUrlInput
            onExtracted={handleExtracted}
            onError={onError}
            disabled={disabled || busy}
            disabledReason={disabledReason}
          />
        )}

        {active === "file" && (
          <JobFileInput
            onExtracted={handleExtracted}
            onError={onError}
            disabled={disabled || busy}
            disabledReason={disabledReason}
          />
        )}

        {active === "csv" && (
          <BatchCsvUpload
            onError={onError}
            disabled={disabled}
            disabledReason={disabledReason}
          />
        )}
      </div>

      <p className="text-xs text-slate-500">
        {TABS.find((t) => t.key === active)?.hint}
      </p>
    </div>
  );
}
