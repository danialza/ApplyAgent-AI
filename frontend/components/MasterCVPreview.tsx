"use client";

import { useEffect, useState } from "react";
import {
  addLibraryProject,
  applyLibraryFix,
  applyMetricAnswers,
  fetchCVLibrary,
  fetchLibraryIssues,
  fetchMetricQuestions,
  ignoreIssue,
  putCVLibrary,
  rebuildLibraryForce,
  renderCV,
  unignoreAllIssues,
  unlockLibrary,
  type LibraryIssue,
  type MetricQuestion,
} from "@/lib/api";
import type { CVLibrary } from "@/lib/types";

interface Props {
  /** Bumped from the parent (UnifiedSourcePanel) whenever a source
   *  add/delete triggers a master rebuild. */
  refreshKey?: number;
  /** Lookup table for source-key → human label (e.g. `cv:1` → "Master CV.pdf").
   *  Built by the parent from the sources list. */
  sourceLabels?: Record<string, string>;
}

const SECTION_LABELS: Record<string, string> = {
  projects: "Projects",
  experience: "Experience",
  publications: "Publications",
  certifications: "Certifications",
  education: "Education",
};

export default function MasterCVPreview({ refreshKey = 0, sourceLabels = {} }: Props) {
  const [lib, setLib] = useState<CVLibrary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<LibraryIssue[]>([]);
  const [issuesLLM, setIssuesLLM] = useState(false);
  const [ignoredCount, setIgnoredCount] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorJson, setEditorJson] = useState("");
  const [editorSaving, setEditorSaving] = useState(false);
  // Master-CV PDF export — renders the full library (no JD) through the
  // same LaTeX renderer the tailor uses, with the page-cap disabled so
  // every project / experience lands.
  const [pdfBusy, setPdfBusy] = useState(false);

  async function downloadMasterPdf() {
    setPdfBusy(true);
    try {
      const data = await renderCV({
        job_text: "", // no JD → unfiltered master CV
        compile_pdf: true,
        use_llm: false, // no JD to tailor against; keep it rule-based + free
        // Show everything — max caps (schema ceiling is 20) + no
        // 2-page shrink.
        max_selected_projects: 20,
        max_additional_projects: 20,
        max_experience: 20,
        enforce_page_cap: false,
      });
      if (!data.pdf_b64) {
        alert(
          data.compile_error
            ? `PDF compile failed: ${data.compile_error}`
            : "No PDF returned."
        );
        return;
      }
      const bytes = Uint8Array.from(atob(data.pdf_b64), (c) =>
        c.charCodeAt(0)
      );
      const blob = new Blob([bytes], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const who = (lib?.header.name || "master")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      a.href = url;
      a.download = `cv-${who || "master"}-master.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Master PDF export failed.");
    } finally {
      setPdfBusy(false);
    }
  }

  function openEditor() {
    if (!lib) return;
    const { id: _id, updated_at: _u, manually_edited_at: _m, ...payload } = lib;
    setEditorJson(JSON.stringify(payload, null, 2));
    setEditorOpen(true);
  }

  async function handleDeleteEntry(section: string, index: number, label: string) {
    if (!confirm(`Remove "${label}" from the master CV?\n\nRecorded as a user-patch — survives future source rebuilds. Undo by editing the library JSON.`)) return;
    try {
      await applyLibraryFix({ kind: "drop_entry", payload: { section, index } });
      window.location.reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
  }

  async function saveEditor() {
    setEditorSaving(true);
    try {
      const parsed = JSON.parse(editorJson);
      await putCVLibrary(parsed);
      setEditorOpen(false);
      window.location.reload();
    } catch (e) {
      alert(
        e instanceof SyntaxError
          ? `Invalid JSON: ${e.message}`
          : e instanceof Error
          ? e.message
          : "Save failed"
      );
    } finally {
      setEditorSaving(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchCVLibrary()
      .then((l) => {
        if (!cancelled) setLib(l);
      })
      .catch((e) => {
        if (!cancelled) {
          setLib(null);
          setError(e instanceof Error ? e.message : "Load failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // Audit in parallel; cheap deterministic + one LLM call.
    fetchLibraryIssues()
      .then((r: any) => {
        if (!cancelled) {
          setIssues(r.issues || []);
          setIssuesLLM(!!r.llm_used);
          setIgnoredCount(r.ignored_count || 0);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (loading) {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-xs text-slate-500">
        Loading master CV…
      </p>
    );
  }
  if (!lib) {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-xs text-slate-500">
        No master CV yet. {error && <span className="text-rose-600">({error})</span>}
        <br />
        Add a source above — CV file, document, or URL — and the master assembles
        automatically.
      </p>
    );
  }

  const errorCount = issues.filter((i) => i.severity === "error").length;
  const warnCount = issues.filter((i) => i.severity === "warning").length;

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-3">
      {/* Quality audit banner — errors red, warnings amber, info slate.
          Click each row to expand its detail + fix hint. */}
      {issues.length > 0 && (
        <details
          className={
            "rounded-lg border p-2 text-xs " +
            (errorCount > 0
              ? "border-rose-300 bg-rose-50 text-rose-900"
              : warnCount > 0
              ? "border-amber-300 bg-amber-50 text-amber-900"
              : "border-slate-200 bg-slate-50 text-slate-700")
          }
          open={errorCount > 0}
        >
          <summary className="cursor-pointer font-semibold">
            ⚠ Master CV audit · {errorCount} errors · {warnCount} warnings ·{" "}
            {issues.length - errorCount - warnCount} info
            {issuesLLM && <span className="ml-2 text-[10px] font-normal opacity-70">(includes LLM analysis)</span>}
            {ignoredCount > 0 && (
              <button
                type="button"
                onClick={async (e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  if (!confirm(`Un-ignore all ${ignoredCount} dismissed issues? They'll re-appear in the next audit.`)) return;
                  try {
                    await unignoreAllIssues();
                    window.location.reload();
                  } catch (err) {
                    alert(err instanceof Error ? err.message : "Reset failed");
                  }
                }}
                className="ml-2 rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50"
                title="Bring back all ignored audit issues"
              >
                Reset {ignoredCount} ignored
              </button>
            )}
          </summary>
          <ul className="mt-2 space-y-1">
            {issues.map((iss, i) => {
              const dot =
                iss.severity === "error" ? "●" : iss.severity === "warning" ? "▲" : "•";
              const cls =
                iss.severity === "error"
                  ? "text-rose-700"
                  : iss.severity === "warning"
                  ? "text-amber-700"
                  : "text-slate-600";
              return (
                <li key={i} className="rounded border border-slate-200 bg-white/60 px-2 py-1">
                  <div className="flex items-start justify-between gap-2">
                    <p className={`font-semibold ${cls}`}>
                      {dot} [{iss.scope}] {iss.title}
                    </p>
                    <div className="flex shrink-0 gap-1">
                      {iss.fix_action && (
                        <button
                          type="button"
                          onClick={async () => {
                            if (!confirm(
                              `Apply this fix?\n\n${iss.fix_action!.preview}\n\nThis edit becomes a user-patch and will re-apply automatically after future source rebuilds — your edits always win.`
                            )) return;
                            try {
                              await applyLibraryFix({
                                kind: iss.fix_action!.kind,
                                payload: iss.fix_action!.payload,
                              });
                              window.location.reload();
                            } catch (e) {
                              alert(e instanceof Error ? e.message : "Apply failed");
                            }
                          }}
                          className="rounded border border-slate-700 bg-slate-800 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-slate-900"
                          title={`Preview: ${iss.fix_action.preview}`}
                        >
                          Apply fix
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await ignoreIssue(iss.fingerprint);
                            // Hide locally without full reload — UX win.
                            setIssues((prev) => prev.filter((x) => x.fingerprint !== iss.fingerprint));
                            setIgnoredCount((n) => n + 1);
                          } catch (e) {
                            alert(e instanceof Error ? e.message : "Ignore failed");
                          }
                        }}
                        className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50"
                        title="Don't show this issue again. Reset via the link in the header."
                      >
                        Ignore
                      </button>
                    </div>
                  </div>
                  {iss.detail && (
                    <p className="text-slate-600">{iss.detail}</p>
                  )}
                  {iss.fix_action?.preview && (
                    <p className="text-[10px] text-emerald-700">
                      ↻ {iss.fix_action.preview}
                    </p>
                  )}
                  {iss.fix_hint && (
                    <p className="italic text-slate-500">↳ {iss.fix_hint}</p>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {/* Lock badge — when set, auto-rebuilds from new sources are
          paused so the user's hand edits / applied fixes stay intact. */}
      {lib.manually_edited_at && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-violet-300 bg-violet-50 px-2 py-1.5 text-xs text-violet-900">
          <p>
            🔒 <strong>Hand-locked</strong> since{" "}
            {lib.manually_edited_at.slice(0, 19).replace("T", " ")}.
            New CV / URL uploads won&apos;t auto-rebuild until you unlock.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={async () => {
                if (!confirm("Unlock the master CV? Hand edits stay; future source uploads will auto-rebuild it.")) return;
                try {
                  await unlockLibrary();
                  window.location.reload();
                } catch (e) {
                  alert(e instanceof Error ? e.message : "Unlock failed");
                }
              }}
              className="rounded border border-violet-600 bg-violet-600 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-violet-700"
            >
              Unlock
            </button>
            <button
              type="button"
              onClick={async () => {
                if (!confirm("Force-rebuild from all sources? This DISCARDS your hand edits.")) return;
                try {
                  await rebuildLibraryForce();
                  window.location.reload();
                } catch (e) {
                  alert(e instanceof Error ? e.message : "Rebuild failed");
                }
              }}
              className="rounded border border-rose-300 bg-white px-2 py-0.5 text-[10px] font-semibold text-rose-700 hover:bg-rose-50"
            >
              Discard & rebuild
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">
            {lib.header.name || "(unnamed)"}
          </p>
          <p className="text-[11px] text-slate-500">
            {[lib.header.location, lib.header.email, lib.header.phone]
              .filter(Boolean)
              .join(" · ") || "—"}
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          {lib.header.linkedin && <a href={lib.header.linkedin} target="_blank" rel="noreferrer" className="underline">LinkedIn</a>}
          {lib.header.github && <a href={lib.header.github} target="_blank" rel="noreferrer" className="underline">GitHub</a>}
          {lib.header.website && <a href={lib.header.website} target="_blank" rel="noreferrer" className="underline">Site</a>}
          <button
            type="button"
            onClick={downloadMasterPdf}
            disabled={pdfBusy}
            className="ml-1 rounded bg-slate-900 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-slate-800 disabled:opacity-60"
            title="Render the full master CV (every project + experience) as a PDF, in the same style as the tailored CV. No 2-page limit."
          >
            {pdfBusy ? "Rendering…" : "Download master PDF"}
          </button>
          <button
            type="button"
            onClick={openEditor}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-100"
            title="Edit master library JSON. Saves with a lock so source uploads won't overwrite."
          >
            Edit library
          </button>
        </div>
      </div>

      {/* Inline editor — full library JSON. Saves lock the master. */}
      {editorOpen && (
        <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          <p>
            Edit JSON below. Save will <strong>lock</strong> the master
            (auto-rebuild from sources paused until you Unlock). Schema
            mirrors <code>CVLibraryBase</code>.
          </p>
          <textarea
            value={editorJson}
            onChange={(e) => setEditorJson(e.target.value)}
            rows={16}
            className="block w-full rounded-md border border-amber-300 bg-white px-2 py-1.5 font-mono text-[11px] text-slate-900 focus:border-amber-500 focus:outline-none"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setEditorOpen(false)}
              className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={saveEditor}
              disabled={editorSaving}
              className="rounded bg-slate-900 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {editorSaving ? "Saving…" : "Save & lock"}
            </button>
          </div>
        </div>
      )}

      {/* Counters strip */}
      <div className="flex flex-wrap gap-1.5 text-[11px]">
        <CounterChip
          label="Projects"
          n={lib.selected_projects.length + lib.additional_projects.length}
        />
        <CounterChip label="Experience" n={lib.experience.length} />
        <CounterChip label="Publications" n={lib.publications.length} />
        <CounterChip label="Certs" n={lib.certifications.length} />
        <CounterChip label="Education" n={lib.education.length} />
        <CounterChip
          label="Skills"
          n={lib.skills_groups.reduce((acc, g) => acc + g.items.length, 0)}
        />
      </div>

      {/* Summary */}
      {lib.summary && (
        <details className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs">
          <summary className="cursor-pointer font-semibold text-slate-700">Summary</summary>
          <p className="mt-1 text-slate-700">{lib.summary}</p>
        </details>
      )}

      {/* Skill groups */}
      {lib.skills_groups.length > 0 && (
        <details className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs" open>
          <summary className="cursor-pointer font-semibold text-slate-700">
            Skills ({lib.skills_groups.length} groups)
          </summary>
          <div className="mt-1 space-y-1">
            {lib.skills_groups.map((g) => (
              <div key={g.label} className="flex flex-wrap items-baseline gap-1">
                <span className="font-semibold text-slate-600">{g.label}:</span>
                <span className="text-slate-700">{g.items.join(", ")}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      <AddProjectCard onAdded={() => window.location.reload()} />

      <MetricsHarvestCard onApplied={() => window.location.reload()} />

      {/* Entry sections — collapsible, per-entry source chips + delete.
          Delete posts a drop_entry user-patch (survives rebuilds). */}
      <EntryList
        title={SECTION_LABELS.projects}
        entries={[
          ...lib.selected_projects.map((p, i) => ({
            line: p.title, period: p.period, tags: p.tags,
            sources: p.sources || [], section: "selected_projects", index: i,
          })),
          ...lib.additional_projects.map((p, i) => ({
            line: p.title, period: p.period, tags: p.tags,
            sources: p.sources || [], section: "additional_projects", index: i,
          })),
        ]}
        sourceLabels={sourceLabels}
        openByDefault
        onDelete={handleDeleteEntry}
      />
      <EntryList
        title={SECTION_LABELS.experience}
        entries={lib.experience.map((x, i) => ({
          line: x.company ? `${x.title} @ ${x.company}` : x.title,
          period: x.period, tags: x.tags, sources: x.sources || [],
          section: "experience", index: i,
        }))}
        sourceLabels={sourceLabels}
        onDelete={handleDeleteEntry}
      />
      <EntryList
        title={SECTION_LABELS.publications}
        entries={lib.publications.map((p, i) => ({
          line: p.status ? `[${p.status}] ${p.title}` : p.title,
          period: "", tags: p.tags, sources: p.sources || [],
          section: "publications", index: i,
        }))}
        sourceLabels={sourceLabels}
        onDelete={handleDeleteEntry}
      />
      <EntryList
        title={SECTION_LABELS.certifications}
        entries={lib.certifications.map((c, i) => ({
          line: c.issuer ? `${c.issuer}: ${c.name}` : c.name,
          period: "", tags: c.tags, sources: c.sources || [],
          section: "certifications", index: i,
        }))}
        sourceLabels={sourceLabels}
        onDelete={handleDeleteEntry}
      />
      <EntryList
        title={SECTION_LABELS.education}
        entries={lib.education.map((e, i) => ({
          line: `${e.institution} — ${e.degree}`,
          period: e.period, tags: [], sources: e.sources || [],
          section: "education", index: i,
        }))}
        sourceLabels={sourceLabels}
        onDelete={handleDeleteEntry}
      />
    </div>
  );
}

function CounterChip({ label, n }: { label: string; n: number }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 ${
        n > 0
          ? "bg-slate-200 font-semibold text-slate-800"
          : "bg-slate-100 text-slate-400"
      }`}
    >
      {label}: {n}
    </span>
  );
}

interface EntryRow {
  line: string;
  period: string;
  tags: string[];
  sources: string[];
  section: string;
  index: number;
}

function EntryList({
  title,
  entries,
  sourceLabels,
  openByDefault = false,
  onDelete,
}: {
  title: string;
  entries: EntryRow[];
  sourceLabels: Record<string, string>;
  openByDefault?: boolean;
  onDelete?: (section: string, index: number, label: string) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <details
      className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs"
      open={openByDefault}
    >
      <summary className="cursor-pointer font-semibold text-slate-700">
        {title} ({entries.length})
      </summary>
      <ul className="mt-1 space-y-1">
        {entries.map((e, i) => (
          <li key={i} className="group flex flex-wrap items-baseline gap-1 text-slate-700">
            {onDelete && (
              <button
                type="button"
                onClick={() => onDelete(e.section, e.index, e.line)}
                className="shrink-0 rounded px-1 text-[11px] font-bold text-rose-400 hover:bg-rose-50 hover:text-rose-700"
                title="Remove from master CV"
              >
                ✕
              </button>
            )}
            <span className="font-medium">{e.line}</span>
            {e.period && <span className="text-[10px] text-slate-500">{e.period}</span>}
            {e.sources.map((sk) => (
              <SourceChip key={sk} sourceKey={sk} label={sourceLabels[sk] || sk} />
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}

function SourceChip({ sourceKey, label }: { sourceKey: string; label: string }) {
  // Colour by kind so multi-source entries are scannable.
  const kind = sourceKey.split(":")[0];
  const cls =
    kind === "cv"
      ? "bg-brand-100 text-brand-800 ring-brand-200"
      : kind === "document"
      ? "bg-amber-100 text-amber-800 ring-amber-200"
      : kind === "web"
      ? "bg-sky-100 text-sky-800 ring-sky-200"
      : "bg-slate-200 text-slate-700 ring-slate-300";
  return (
    <span
      title={`Source: ${sourceKey}`}
      className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ring-1 ${cls}`}
    >
      {label}
    </span>
  );
}


/** Form to add a single project. Posts to /library/add-project — the
 *  backend pulls the URL (GitHub repo / web / paper), runs an LLM
 *  enricher, and persists the result as an add_entry user-patch so
 *  the project survives master rebuilds. */
function AddProjectCard({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [period, setPeriod] = useState("");
  const [notes, setNotes] = useState("");
  const [tagHints, setTagHints] = useState("");
  const [section, setSection] = useState<"selected_projects" | "additional_projects">(
    "selected_projects"
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setErr(null);
    if (!title.trim()) {
      setErr("Title required");
      return;
    }
    setBusy(true);
    try {
      await addLibraryProject({
        title: title.trim(),
        url: url.trim(),
        period: period.trim(),
        notes: notes.trim(),
        tag_hints: tagHints
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        section,
      });
      onAdded();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Add failed");
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-2 text-xs">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="font-semibold text-brand-700 hover:text-brand-900"
        >
          + Add project (paste link, LLM fills the rest)
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-brand-200 bg-brand-50 p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-brand-800">Add project</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-slate-500 hover:text-slate-800"
        >
          ✕
        </button>
      </div>
      <input
        type="text"
        placeholder="Title* (e.g. Patina — iOS AR Companion)"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        className="w-full rounded border border-slate-300 px-2 py-1"
      />
      <input
        type="url"
        placeholder="Link (GitHub repo / paper / demo / site)"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        className="w-full rounded border border-slate-300 px-2 py-1"
      />
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Period (e.g. Jan 2025 – Mar 2025)"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="flex-1 rounded border border-slate-300 px-2 py-1"
        />
        <select
          value={section}
          onChange={(e) =>
            setSection(
              e.target.value as "selected_projects" | "additional_projects"
            )
          }
          className="rounded border border-slate-300 bg-white px-2 py-1"
        >
          <option value="selected_projects">Selected</option>
          <option value="additional_projects">Additional</option>
        </select>
      </div>
      <textarea
        placeholder="Notes — free-form prose. What did it do, what stack, what outcome. The LLM mines this + the URL for bullets."
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={4}
        className="w-full rounded border border-slate-300 px-2 py-1"
      />
      <input
        type="text"
        placeholder="Tag hints (comma-separated, e.g. VLA, MuJoCo, RL)"
        value={tagHints}
        onChange={(e) => setTagHints(e.target.value)}
        className="w-full rounded border border-slate-300 px-2 py-1"
      />
      {err && <div className="text-red-600">{err}</div>}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          disabled={busy}
          className="rounded border border-slate-300 bg-white px-3 py-1 font-medium text-slate-700 hover:bg-slate-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={busy || !title.trim()}
          className="rounded bg-brand-600 px-3 py-1 font-semibold text-white shadow-sm hover:bg-brand-700 disabled:opacity-60"
        >
          {busy ? "Enriching…" : "Generate + Add"}
        </button>
      </div>
    </div>
  );
}

/** Metrics harvester — asks for real numbers on unquantified bullets,
 *  weaves them in via LLM, persists as user-patches. Each answer is
 *  remembered (candidate_facts) so it's never asked twice. */
function MetricsHarvestCard({ onApplied }: { onApplied: () => void }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [questions, setQuestions] = useState<MetricQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [applying, setApplying] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setOpen(true);
    setLoading(true);
    setErr(null);
    try {
      setQuestions(await fetchMetricQuestions());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load questions.");
    } finally {
      setLoading(false);
    }
  }

  async function apply() {
    const payload = questions
      .filter((q) => (answers[q.key] || "").trim())
      .map((q) => ({ ...q, answer: answers[q.key].trim() }));
    if (payload.length === 0) {
      setErr("Answer at least one question (skip the ones you don't know).");
      return;
    }
    setApplying(true);
    setErr(null);
    try {
      const res = await applyMetricAnswers(payload);
      if ((res.applied || []).length > 0) {
        onApplied();
      } else {
        setErr("No bullets were updated — try rephrasing an answer with a concrete number.");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Apply failed.");
    } finally {
      setApplying(false);
    }
  }

  if (!open) {
    return (
      <div className="rounded-lg border border-dashed border-emerald-300 bg-emerald-50 p-2 text-xs">
        <button
          type="button"
          onClick={load}
          className="font-semibold text-emerald-700 hover:text-emerald-900"
        >
          📈 Strengthen with numbers (recruiters rank quantified impact first)
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-emerald-900">
          Strengthen with numbers
        </span>
        <button type="button" onClick={() => setOpen(false)} className="text-slate-500 hover:text-slate-800">✕</button>
      </div>
      <p className="text-emerald-800">
        Answer with a real number (estimate honestly). Each answer is woven into
        its bullet, saved permanently, and never asked again.
      </p>
      {loading && <p className="text-slate-500">Scanning bullets…</p>}
      {!loading && questions.length === 0 && (
        <p className="text-slate-600">Nothing to ask — every bullet already carries a number, or the LLM found no gaps worth quantifying. ✓</p>
      )}
      {questions.map((q) => (
        <label key={q.key} className="block">
          <span className="font-medium text-slate-700">{q.question}</span>
          <input
            type="text"
            value={answers[q.key] || ""}
            onChange={(e) => setAnswers((a) => ({ ...a, [q.key]: e.target.value }))}
            placeholder="e.g. ~40% faster · 2K queries/day · 12 analysts"
            className="mt-1 w-full rounded border border-emerald-300 px-2 py-1"
          />
        </label>
      ))}
      {err && <p className="text-rose-600">{err}</p>}
      {questions.length > 0 && (
        <button
          type="button"
          onClick={apply}
          disabled={applying}
          className="rounded bg-emerald-600 px-3 py-1.5 font-semibold text-white hover:bg-emerald-700 disabled:opacity-60"
        >
          {applying ? "Weaving numbers in…" : "Apply to master CV"}
        </button>
      )}
    </div>
  );
}
