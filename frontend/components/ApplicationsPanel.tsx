"use client";

import { useEffect, useState } from "react";
import {
  applicationCvPdfUrl,
  applicationCvTexUrl,
  applicationsCsvUrl,
  deleteApplication,
  listApplications,
  updateApplication,
} from "@/lib/api";
import type { Application } from "@/lib/types";

interface Props {
  onError: (msg: string) => void;
  /** Bump this number whenever an external action (e.g. section 5
   *  "Add to tracker") creates a row, so the panel refetches. */
  refreshKey?: number;
}

const STATUS_OPTIONS = [
  "To-Apply",
  "Applied",
  "Interview",
  "Offer",
  "Rejected",
  "Skipped",
];

const HOW_OPTIONS = ["form", "indeed", "linkedin", "referral", "email", "other"];

export default function ApplicationsPanel({ onError, refreshKey = 0 }: Props) {
  const [rows, setRows] = useState<Application[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState("");

  async function refresh() {
    setLoading(true);
    try {
      setRows(await listApplications());
    } catch (err) {
      onError(err instanceof Error ? err.message : "Load failed.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function patch(id: number, field: keyof Application, value: string) {
    setSavingId(id);
    // Optimistic local update so the dropdown/input feels instant; if
    // the PATCH fails we revert via a full refresh.
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, [field]: value } : r))
    );
    try {
      await updateApplication(id, { [field]: value } as any);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Save failed.");
      refresh();
    } finally {
      setSavingId(null);
    }
  }

  async function remove(id: number) {
    if (!confirm("Delete this application row?")) return;
    try {
      await deleteApplication(id);
      setRows((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      onError(err instanceof Error ? err.message : "Delete failed.");
    }
  }

  const filtered = statusFilter
    ? rows.filter((r) => r.status === statusFilter)
    : rows;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs font-medium text-slate-600">
          Status filter:
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="ml-2 rounded border-slate-300 bg-white px-1.5 py-0.5 text-xs"
          >
            <option value="">All ({rows.length})</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s} ({rows.filter((r) => r.status === s).length})
              </option>
            ))}
          </select>
        </label>
        <a
          href={applicationsCsvUrl()}
          className="ml-auto rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
          download
        >
          Export CSV
        </a>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {filtered.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-sm text-slate-500">
          No applications tracked yet. Render a tailored CV in section 5
          and click <em>Track this application</em> to add one.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-xs">
            <thead className="bg-slate-50 text-left font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-2 py-1.5">When</th>
                <th className="px-2 py-1.5">Deadline</th>
                <th className="px-2 py-1.5">Where?</th>
                <th className="px-2 py-1.5">What?</th>
                <th className="px-2 py-1.5">Status</th>
                <th className="px-2 py-1.5">How</th>
                <th className="px-2 py-1.5">Link</th>
                <th className="px-2 py-1.5">CV</th>
                <th className="px-2 py-1.5">Notes</th>
                <th className="px-2 py-1.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {filtered.map((r) => (
                <tr key={r.id} className={savingId === r.id ? "opacity-50" : ""}>
                  <td className="px-2 py-1.5">
                    <DateCell
                      value={r.apply_date}
                      onChange={(v) => patch(r.id, "apply_date", v)}
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <DateCell
                      value={r.deadline}
                      onChange={(v) => patch(r.id, "deadline", v)}
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <TextCell
                      value={r.company}
                      onChange={(v) => patch(r.id, "company", v)}
                      placeholder="-"
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <TextCell
                      value={r.role}
                      onChange={(v) => patch(r.id, "role", v)}
                      placeholder="-"
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <select
                      value={r.status}
                      onChange={(e) => patch(r.id, "status", e.target.value)}
                      className="rounded border-slate-300 bg-white px-1 py-0.5 text-xs"
                    >
                      {!STATUS_OPTIONS.includes(r.status) && (
                        <option value={r.status}>{r.status}</option>
                      )}
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2 py-1.5">
                    <select
                      value={r.how}
                      onChange={(e) => patch(r.id, "how", e.target.value)}
                      className="rounded border-slate-300 bg-white px-1 py-0.5 text-xs"
                    >
                      <option value="">-</option>
                      {HOW_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                      {r.how && !HOW_OPTIONS.includes(r.how) && (
                        <option value={r.how}>{r.how}</option>
                      )}
                    </select>
                  </td>
                  <td className="max-w-[14rem] px-2 py-1.5">
                    <div className="flex items-center gap-1">
                      <TextCell
                        value={r.url}
                        onChange={(v) => patch(r.id, "url", v)}
                        placeholder="-"
                      />
                      {r.url && (
                        <a
                          href={r.url}
                          target="_blank"
                          rel="noreferrer"
                          className="shrink-0 rounded border border-slate-200 px-1 text-[10px] text-slate-600 hover:bg-slate-50"
                          title="Open in new tab"
                        >
                          ↗
                        </a>
                      )}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    {r.has_cv_pdf && (
                      <a
                        href={applicationCvPdfUrl(r.id)}
                        target="_blank"
                        rel="noreferrer"
                        className="mr-1 rounded border border-rose-300 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700 hover:bg-rose-50"
                        title="Download the tailored PDF snapshot"
                      >
                        PDF
                      </a>
                    )}
                    {r.has_cv_latex && (
                      <a
                        href={applicationCvTexUrl(r.id)}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600 hover:bg-slate-50"
                        title="Download the tailored .tex source"
                      >
                        TeX
                      </a>
                    )}
                    {!r.has_cv_latex && !r.has_cv_pdf && (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="max-w-[10rem] px-2 py-1.5">
                    <TextCell
                      value={r.notes}
                      onChange={(v) => patch(r.id, "notes", v)}
                      placeholder="-"
                    />
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <button
                      type="button"
                      onClick={() => remove(r.id)}
                      className="text-rose-600 hover:underline"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- Inline-edit primitives ----

function TextCell({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <input
      type="text"
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => {
        if (local !== value) onChange(local);
      }}
      placeholder={placeholder}
      className="w-full rounded border-transparent bg-transparent px-1 py-0.5 text-xs focus:border-slate-300 focus:bg-white focus:outline-none"
    />
  );
}

function DateCell({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="date"
      value={value || ""}
      onChange={(e) => onChange(e.target.value)}
      className="rounded border-transparent bg-transparent px-1 py-0.5 text-xs focus:border-slate-300 focus:bg-white focus:outline-none"
    />
  );
}
