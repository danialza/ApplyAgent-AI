"use client";

import { useState } from "react";
import QueryTagEditor from "@/components/QueryTagEditor";
import { runAgent } from "@/lib/api";
import type {
  AgentRunResponse,
  AgentStep,
  DiscoveredJob,
  QueryTagSet,
  RankedJobResult,
  TailorResponse,
} from "@/lib/types";

interface Props {
  onError: (message: string) => void;
  disabled?: boolean;
  disabledReason?: string;
}

const PIPELINE_STEPS = ["profile", "queries", "discovery", "ranking", "tailoring"] as const;

export default function AgentDashboard({ onError, disabled, disabledReason }: Props) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AgentRunResponse | null>(null);
  const [maxDiscover, setMaxDiscover] = useState(30);
  const [maxRank, setMaxRank] = useState(15);
  const [maxTailor, setMaxTailor] = useState(5);
  const [showEditor, setShowEditor] = useState(false);
  // Active overrides — null until the user mutates them in the editor.
  const [override, setOverride] = useState<{ queries: string[]; tags: QueryTagSet } | null>(
    null,
  );

  async function handleRun() {
    setBusy(true);
    try {
      const data = await runAgent({
        max_discover: maxDiscover,
        max_rank: maxRank,
        max_tailor: maxTailor,
        ...(override ? { queries: override.queries, tags: override.tags } : {}),
      });
      setResult(data);
      if (data.error) onError(data.error);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Agent run failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <NumberKnob label="Discover" value={maxDiscover} setValue={setMaxDiscover} min={1} max={100} />
        <NumberKnob label="Rank" value={maxRank} setValue={setMaxRank} min={1} max={100} />
        <NumberKnob label="Tailor" value={maxTailor} setValue={setMaxTailor} min={1} max={20} />

        <button
          type="button"
          onClick={handleRun}
          disabled={busy || disabled}
          className="ml-auto inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Running…" : "Run agent"}
        </button>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          {disabled && disabledReason
            ? disabledReason
            : "Runs the full pipeline: derives queries from your profile, fetches jobs from public APIs (RemoteOK, Remotive, HN), ranks them against your CVs, and produces tailoring suggestions for the top matches."}
        </p>
        <button
          type="button"
          onClick={() => setShowEditor((v) => !v)}
          disabled={disabled}
          className="ml-3 shrink-0 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {showEditor ? "Hide editor" : override ? "Edit overrides" : "Edit queries & tags"}
        </button>
      </div>

      {showEditor && (
        <QueryTagEditor
          onChange={(next) => setOverride(next)}
          onError={onError}
        />
      )}

      {override && !showEditor && (
        <p className="text-xs italic text-amber-700">
          Using your overrides: {override.queries.length} queries, {override.tags.roles.length} roles.
        </p>
      )}

      <StepsTrace busy={busy} steps={result?.steps ?? []} />

      {result && (
        <div className="space-y-5">
          {result.queries.length > 0 && <QueryPanel queries={result.queries} tags={result.tags} />}
          {result.discovered.length > 0 && <DiscoveredPanel jobs={result.discovered} />}
          {result.ranked.length > 0 && <RankedPanel results={result.ranked} />}
          {result.tailored.length > 0 && <TailoredPanel bundles={result.tailored} />}
        </div>
      )}
    </div>
  );
}

// ---------- knob ----------

function NumberKnob({
  label,
  value,
  setValue,
  min,
  max,
}: {
  label: string;
  value: number;
  setValue: (v: number) => void;
  min: number;
  max: number;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          if (Number.isFinite(n)) setValue(Math.min(max, Math.max(min, n)));
        }}
        className="w-20 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
      />
    </label>
  );
}

// ---------- progress trace ----------

function StepsTrace({ busy, steps }: { busy: boolean; steps: AgentStep[] }) {
  // Keep all 5 phases visible even before a run so the user can see the pipeline shape.
  const byName = new Map(steps.map((s) => [s.name, s]));
  return (
    <ol className="grid gap-2 sm:grid-cols-5">
      {PIPELINE_STEPS.map((name, idx) => {
        const step = byName.get(name);
        const status = step?.status ?? (busy && idx === steps.length ? "running" : "pending");
        return (
          <li
            key={name}
            className={`rounded-lg border p-3 text-xs transition ${stepClass(status)}`}
          >
            <div className="flex items-center gap-2">
              <span className="text-base">{stepIcon(status)}</span>
              <span className="font-semibold capitalize">{name}</span>
            </div>
            {step?.detail && <div className="mt-1 text-slate-600">{step.detail}</div>}
            {!step && status === "running" && <div className="mt-1 text-slate-500">working…</div>}
            {!step && status === "pending" && <div className="mt-1 text-slate-400">pending</div>}
          </li>
        );
      })}
    </ol>
  );
}

function stepClass(status: string): string {
  switch (status) {
    case "ok":
      return "border-emerald-200 bg-emerald-50";
    case "skipped":
      return "border-amber-200 bg-amber-50";
    case "error":
      return "border-rose-200 bg-rose-50";
    case "running":
      return "border-brand-200 bg-brand-50 animate-pulse";
    default:
      return "border-slate-200 bg-white";
  }
}

function stepIcon(status: string): string {
  switch (status) {
    case "ok":
      return "✓";
    case "skipped":
      return "↷";
    case "error":
      return "✗";
    case "running":
      return "…";
    default:
      return "·";
  }
}

// ---------- panels ----------

function QueryPanel({
  queries,
  tags,
}: {
  queries: string[];
  tags: import("@/lib/types").QueryTagSet;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">Smart queries</h3>
      <ul className="mt-2 space-y-1 text-sm text-slate-700">
        {queries.map((q, i) => (
          <li key={i} className="rounded bg-slate-50 px-2 py-1 font-mono">
            {q}
          </li>
        ))}
      </ul>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <ChipList label="Roles" items={tags.roles} />
        <ChipList label="Skills" items={tags.skills} />
        <ChipList label="Tools" items={tags.tools} />
      </div>
    </section>
  );
}

function ChipList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {items.slice(0, 8).map((item) => (
          <span
            key={item}
            className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200"
          >
            {item}
          </span>
        ))}
        {items.length > 8 && (
          <span className="text-xs text-slate-400">+{items.length - 8}</span>
        )}
      </div>
    </div>
  );
}

function DiscoveredPanel({ jobs }: { jobs: DiscoveredJob[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">
        Discovered jobs <span className="font-normal text-slate-400">({jobs.length})</span>
      </h3>
      <ul className="mt-2 divide-y divide-slate-100">
        {jobs.slice(0, 8).map((j, i) => (
          <li key={`${j.url}-${i}`} className="py-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-slate-900">{j.title}</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs uppercase text-slate-600">
                {j.source}
              </span>
            </div>
            <div className="text-xs text-slate-500">
              {[j.company, j.location].filter(Boolean).join(" · ")}
            </div>
            {j.matched_terms.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {j.matched_terms.slice(0, 6).map((t) => (
                  <span
                    key={t}
                    className="rounded-full bg-brand-50 px-2 py-0.5 text-xs text-brand-700 ring-1 ring-brand-200"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
      {jobs.length > 8 && (
        <p className="mt-1 text-xs text-slate-400">+{jobs.length - 8} more</p>
      )}
    </section>
  );
}

function tierColor(value: number): string {
  if (value >= 75) return "bg-emerald-100 text-emerald-700";
  if (value >= 50) return "bg-amber-100 text-amber-700";
  return "bg-rose-100 text-rose-700";
}

function RankedPanel({ results }: { results: RankedJobResult[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">
        Ranked <span className="font-normal text-slate-400">(top {results.length})</span>
      </h3>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="py-1.5 font-medium">Job</th>
              <th className="py-1.5 font-medium">Best CV</th>
              <th className="py-1.5 font-medium">Score</th>
              <th className="py-1.5 font-medium">Missing</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {results.slice(0, 10).map((r, i) => (
              <tr key={`${r.job.url}-${i}`}>
                <td className="py-2">
                  <div className="font-medium text-slate-900">{r.job.title || "Untitled role"}</div>
                  <div className="text-xs text-slate-500">
                    {[r.job.company, r.job.location, r.job.source].filter(Boolean).join(" · ")}
                  </div>
                </td>
                <td className="py-2 text-xs text-slate-600">
                  {r.best_cv_name || r.best_cv_filename || "—"}
                </td>
                <td className="py-2">
                  <span
                    className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${tierColor(r.overall_score)}`}
                  >
                    {r.overall_score.toFixed(0)}
                  </span>
                </td>
                <td className="py-2">
                  <div className="flex flex-wrap gap-1">
                    {r.missing_skills.slice(0, 4).map((s) => (
                      <span
                        key={s}
                        className="rounded-full bg-rose-50 px-2 py-0.5 text-xs text-rose-700 ring-1 ring-rose-200"
                      >
                        {s}
                      </span>
                    ))}
                    {r.missing_skills.length === 0 && (
                      <span className="text-xs text-emerald-600">none</span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TailoredPanel({ bundles }: { bundles: TailorResponse[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">
        Tailoring suggestions <span className="font-normal text-slate-400">(top {bundles.length})</span>
      </h3>
      <div className="mt-2 space-y-4">
        {bundles.map((b, i) => (
          <article key={i} className="rounded-md border border-slate-200 bg-slate-50/50 p-3">
            <header className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="font-semibold text-slate-900">{b.job.job_title || "Role"}</div>
                <div className="text-xs text-slate-500">
                  {[b.job.company, b.job.location].filter(Boolean).join(" · ")}
                </div>
              </div>
              <span
                className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${tierColor(b.match.overall_score)}`}
              >
                {b.match.overall_score.toFixed(0)}
              </span>
            </header>

            {b.suggestions.summary_hint && (
              <p className="mt-2 text-xs italic text-slate-600">"{b.suggestions.summary_hint}"</p>
            )}

            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <ChipBlock
                label="Skills to add"
                items={b.suggestions.skills_to_add}
                tone="rose"
              />
              <ChipBlock
                label="Skills to emphasize"
                items={b.suggestions.skills_to_emphasize}
                tone="amber"
              />
              <ChipBlock
                label="ATS keywords"
                items={b.suggestions.keywords_for_ats.slice(0, 12)}
                tone="slate"
              />
              {b.suggestions.sections_to_add.length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                    Sections to add
                  </p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-slate-600">
                    {b.suggestions.sections_to_add.map((s, j) => (
                      <li key={j}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {b.suggestions.bullets_to_rewrite.length > 0 && (
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer font-semibold text-slate-700">
                  Rewrite suggestions ({b.suggestions.bullets_to_rewrite.length})
                </summary>
                <ul className="mt-2 space-y-2">
                  {b.suggestions.bullets_to_rewrite.map((r, j) => (
                    <li key={j} className="rounded bg-white p-2 ring-1 ring-slate-200">
                      <div className="text-slate-700">{r.original}</div>
                      <div className="mt-1 text-slate-500">
                        Target: {r.target_skills.join(", ")}
                      </div>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function ChipBlock({
  label,
  items,
  tone,
}: {
  label: string;
  items: string[];
  tone: "rose" | "amber" | "slate" | "emerald";
}) {
  if (items.length === 0) return null;
  const toneClass = {
    rose: "bg-rose-50 text-rose-700 ring-rose-200",
    amber: "bg-amber-50 text-amber-700 ring-amber-200",
    slate: "bg-slate-100 text-slate-700 ring-slate-200",
    emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  }[tone];
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {items.map((item) => (
          <span
            key={item}
            className={`rounded-full px-2 py-0.5 text-xs ring-1 ${toneClass}`}
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}
