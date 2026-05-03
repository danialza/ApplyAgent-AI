"use client";

import { useRef, useState } from "react";
import { batchMatchCsv } from "@/lib/api";
import type { BatchMatchResponse, BatchMatchRow } from "@/lib/types";

interface Props {
  onError: (message: string) => void;
  disabled?: boolean;
  disabledReason?: string;
}

const EXPORT_COLUMNS: { key: keyof BatchMatchRow | "best_cv"; label: string }[] = [
  { key: "row_index", label: "row" },
  { key: "job_title", label: "job_title" },
  { key: "company", label: "company" },
  { key: "location", label: "location" },
  { key: "best_cv", label: "best_cv" },
  { key: "best_score", label: "best_score" },
  { key: "matched_skills", label: "matched_skills" },
  { key: "missing_skills", label: "missing_skills" },
  { key: "strongest_points", label: "strongest_points" },
  { key: "url", label: "url" },
  { key: "error", label: "error" },
];

function csvEscape(value: unknown): string {
  const s = Array.isArray(value) ? value.join(" | ") : value == null ? "" : String(value);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function rowsToCsv(rows: BatchMatchRow[]): string {
  const header = EXPORT_COLUMNS.map((c) => c.label).join(",");
  const lines = rows.map((r) =>
    EXPORT_COLUMNS.map((c) => {
      if (c.key === "best_cv") return csvEscape(r.best_cv_name || r.best_cv_filename);
      return csvEscape(r[c.key as keyof BatchMatchRow]);
    }).join(","),
  );
  return [header, ...lines].join("\n");
}

function downloadBlob(filename: string, data: string, mime: string) {
  const blob = new Blob([data], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function BatchCsvUpload({ onError, disabled, disabledReason }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchMatchResponse | null>(null);

  async function handleFile(file: File) {
    if (!file.name.toLowerCase().endsWith(".csv")) {
      onError("Please choose a .csv file.");
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const data = await batchMatchCsv(file);
      if (data.error) {
        onError(data.error);
        return;
      }
      setResult(data);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Batch match failed.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function handleExport() {
    if (!result || result.rows.length === 0) return;
    downloadBlob("batch-match-results.csv", rowsToCsv(result.rows), "text/csv");
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy || disabled}
          className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Matching…" : "Upload jobs CSV"}
        </button>
        {result && result.rows.length > 0 && (
          <button
            type="button"
            onClick={handleExport}
            className="inline-flex items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
          >
            Export results CSV
          </button>
        )}
        <p className="text-xs text-slate-500">
          {disabled && disabledReason
            ? disabledReason
            : "Required column: description. Optional: job_title, company, location, url, salary, employment_type. Limit 100 rows."}
        </p>
      </div>

      {result && (
        <div className="space-y-2">
          <p className="text-xs text-slate-500">
            Processed {result.rows_processed} job{result.rows_processed === 1 ? "" : "s"}
            {result.rows_skipped > 0 && ` · skipped ${result.rows_skipped}`}
            {result.truncated && " · truncated at 100 rows"}
          </p>
          <BatchTable rows={result.rows} />
        </div>
      )}
    </div>
  );
}

function tierColor(value: number): string {
  if (value >= 75) return "bg-emerald-100 text-emerald-700";
  if (value >= 50) return "bg-amber-100 text-amber-700";
  return "bg-rose-100 text-rose-700";
}

function BatchTable({ rows }: { rows: BatchMatchRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500">
        No rows in CSV.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full min-w-[900px] text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
          <tr>
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Job</th>
            <th className="px-3 py-2 font-medium">Best CV</th>
            <th className="px-3 py-2 font-medium">Score</th>
            <th className="px-3 py-2 font-medium">Missing</th>
            <th className="px-3 py-2 font-medium">Strongest</th>
            <th className="px-3 py-2 font-medium">Link</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 bg-white">
          {rows.map((r) => (
            <tr key={r.row_index} className={r.error ? "bg-rose-50/40" : ""}>
              <td className="px-3 py-2 text-xs text-slate-500">{r.row_index}</td>
              <td className="px-3 py-2">
                <div className="font-medium text-slate-900">
                  {r.job_title || "Untitled role"}
                </div>
                <div className="text-xs text-slate-500">
                  {[r.company, r.location].filter(Boolean).join(" · ") || "—"}
                </div>
                {r.error && (
                  <div className="mt-1 text-xs text-rose-600">{r.error}</div>
                )}
              </td>
              <td className="px-3 py-2">
                {r.best_cv_id ? (
                  <div>
                    <div className="font-medium text-slate-900">
                      {r.best_cv_name || r.best_cv_filename}
                    </div>
                    <div className="text-xs text-slate-500">{r.best_cv_filename}</div>
                  </div>
                ) : (
                  <span className="text-xs text-slate-400">—</span>
                )}
              </td>
              <td className="px-3 py-2">
                {r.best_cv_id ? (
                  <span
                    className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${tierColor(r.best_score)}`}
                  >
                    {r.best_score.toFixed(0)}
                  </span>
                ) : (
                  <span className="text-xs text-slate-400">—</span>
                )}
              </td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {r.missing_skills.slice(0, 4).map((s) => (
                    <span
                      key={s}
                      className="rounded-full bg-rose-50 px-2 py-0.5 text-xs text-rose-700 ring-1 ring-rose-200"
                    >
                      {s}
                    </span>
                  ))}
                  {r.missing_skills.length > 4 && (
                    <span className="text-xs text-slate-400">
                      +{r.missing_skills.length - 4}
                    </span>
                  )}
                  {r.missing_skills.length === 0 && r.best_cv_id && (
                    <span className="text-xs text-emerald-600">none</span>
                  )}
                </div>
              </td>
              <td className="px-3 py-2 text-xs text-slate-600">
                {r.strongest_points.length === 0 ? (
                  <span className="text-slate-400">—</span>
                ) : (
                  <ul className="space-y-0.5">
                    {r.strongest_points.slice(0, 2).map((p, i) => (
                      <li key={i} className="line-clamp-2">
                        {p}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
              <td className="px-3 py-2 text-xs">
                {r.url ? (
                  <a
                    href={r.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-brand-600 hover:underline"
                  >
                    open
                  </a>
                ) : (
                  <span className="text-slate-400">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
