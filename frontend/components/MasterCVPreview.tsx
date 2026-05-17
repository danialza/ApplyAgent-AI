"use client";

import { useEffect, useState } from "react";
import {
  applyLibraryFix,
  fetchCVLibrary,
  fetchLibraryIssues,
  rebuildLibraryForce,
  unlockLibrary,
  type LibraryIssue,
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
      .then((r) => {
        if (!cancelled) {
          setIssues(r.issues || []);
          setIssuesLLM(!!r.llm_used);
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
                    {iss.fix_action && (
                      <button
                        type="button"
                        onClick={async () => {
                          if (!confirm(
                            `Apply this fix?\n\n${iss.fix_action!.preview}\n\nThis will lock the master CV (auto-rebuild paused until you unlock).`
                          )) return;
                          try {
                            await applyLibraryFix({
                              kind: iss.fix_action!.kind,
                              payload: iss.fix_action!.payload,
                            });
                            // Bumping refreshKey is the parent's job;
                            // simplest path: force a reload of audit + lib.
                            window.location.reload();
                          } catch (e) {
                            alert(e instanceof Error ? e.message : "Apply failed");
                          }
                        }}
                        className="shrink-0 rounded border border-slate-700 bg-slate-800 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-slate-900"
                        title={`Preview: ${iss.fix_action.preview}`}
                      >
                        Apply fix
                      </button>
                    )}
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
        <div className="flex gap-2 text-[11px] text-slate-500">
          {lib.header.linkedin && <a href={lib.header.linkedin} target="_blank" rel="noreferrer" className="underline">LinkedIn</a>}
          {lib.header.github && <a href={lib.header.github} target="_blank" rel="noreferrer" className="underline">GitHub</a>}
          {lib.header.website && <a href={lib.header.website} target="_blank" rel="noreferrer" className="underline">Site</a>}
        </div>
      </div>

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

      {/* Entry sections — collapsible with per-entry source chips */}
      <EntryList
        title={SECTION_LABELS.projects}
        entries={[...lib.selected_projects, ...lib.additional_projects].map((p) => ({
          line: p.title,
          period: p.period,
          tags: p.tags,
          sources: p.sources || [],
        }))}
        sourceLabels={sourceLabels}
        openByDefault
      />
      <EntryList
        title={SECTION_LABELS.experience}
        entries={lib.experience.map((x) => ({
          line: x.company ? `${x.title} @ ${x.company}` : x.title,
          period: x.period,
          tags: x.tags,
          sources: x.sources || [],
        }))}
        sourceLabels={sourceLabels}
      />
      <EntryList
        title={SECTION_LABELS.publications}
        entries={lib.publications.map((p) => ({
          line: p.status ? `[${p.status}] ${p.title}` : p.title,
          period: "",
          tags: p.tags,
          sources: p.sources || [],
        }))}
        sourceLabels={sourceLabels}
      />
      <EntryList
        title={SECTION_LABELS.certifications}
        entries={lib.certifications.map((c) => ({
          line: c.issuer ? `${c.issuer}: ${c.name}` : c.name,
          period: "",
          tags: c.tags,
          sources: c.sources || [],
        }))}
        sourceLabels={sourceLabels}
      />
      <EntryList
        title={SECTION_LABELS.education}
        entries={lib.education.map((e) => ({
          line: `${e.institution} — ${e.degree}`,
          period: e.period,
          tags: [],
          sources: e.sources || [],
        }))}
        sourceLabels={sourceLabels}
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

function EntryList({
  title,
  entries,
  sourceLabels,
  openByDefault = false,
}: {
  title: string;
  entries: { line: string; period: string; tags: string[]; sources: string[] }[];
  sourceLabels: Record<string, string>;
  openByDefault?: boolean;
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
          <li key={i} className="flex flex-wrap items-baseline gap-1 text-slate-700">
            <span className="font-medium">· {e.line}</span>
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
