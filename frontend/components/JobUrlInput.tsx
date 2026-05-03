"use client";

import { useState } from "react";
import { parseJobFromUrl } from "@/lib/api";
import type { JobParsed } from "@/lib/types";

interface Props {
  /** Called when scraping + parsing succeed. The page can then enable matching. */
  onExtracted: (extractedText: string, parsed: JobParsed) => void;
  onError: (message: string) => void;
  /** Disabled when no CVs are uploaded yet. */
  disabled?: boolean;
  disabledReason?: string;
}

export default function JobUrlInput({
  onExtracted,
  onError,
  disabled,
  disabledReason,
}: Props) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ parsed: JobParsed; notes: string[] } | null>(null);

  const looksLikeUrl = /^https?:\/\/\S+\.\S+/.test(url.trim());
  const canSubmit = !busy && !disabled && looksLikeUrl;

  async function handleAnalyse() {
    setBusy(true);
    setPreview(null);
    try {
      const result = await parseJobFromUrl(url.trim());
      if (!result.success || !result.parsed_job) {
        onError(
          result.error ||
            "Could not extract a job description from the URL. Please paste it manually instead.",
        );
        return;
      }
      setPreview({ parsed: result.parsed_job, notes: result.notes });
      onExtracted(result.extracted_text, result.parsed_job);
    } catch (err) {
      onError(err instanceof Error ? err.message : "URL analysis failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://company.com/careers/senior-ai-engineer"
          className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
        />
        <button
          type="button"
          disabled={!canSubmit}
          onClick={handleAnalyse}
          className="inline-flex items-center justify-center whitespace-nowrap rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Fetching…" : "Analyse Job URL"}
        </button>
      </div>

      <p className="text-xs text-slate-500">
        {disabled && disabledReason
          ? disabledReason
          : "We respect robots.txt and skip pages that block scraping. If extraction fails, paste the JD manually below."}
      </p>

      {preview && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
            Extracted preview
          </p>
          <p className="mt-1 font-semibold text-slate-900">
            {preview.parsed.job_title || "Untitled role"}
          </p>
          <p className="text-xs text-slate-600">
            {[preview.parsed.company, preview.parsed.location].filter(Boolean).join(" · ") ||
              "No company / location detected"}
          </p>
          {preview.parsed.required_skills.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {preview.parsed.required_skills.slice(0, 8).map((s) => (
                <span
                  key={s}
                  className="rounded-full bg-white px-2 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200"
                >
                  {s}
                </span>
              ))}
              {preview.parsed.required_skills.length > 8 && (
                <span className="text-xs text-slate-500">
                  +{preview.parsed.required_skills.length - 8} more
                </span>
              )}
            </div>
          )}
          {preview.notes.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs text-emerald-700">
              {preview.notes.map((n, i) => (
                <li key={i}>· {n}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
