"use client";

import { useState } from "react";
import {
  applyEvidenceAnswers,
  fetchEvidenceQuestions,
  type EvidenceQuestion,
} from "@/lib/api";
import type { Scorecard, SkillEvidence } from "@/lib/types";

const VERDICT_STYLE: Record<string, string> = {
  strong: "bg-emerald-100 text-emerald-900 ring-emerald-300",
  "worth-applying": "bg-sky-100 text-sky-900 ring-sky-300",
  weak: "bg-amber-100 text-amber-900 ring-amber-300",
  "no-go": "bg-rose-100 text-rose-900 ring-rose-300",
};

const VERDICT_LABEL: Record<string, string> = {
  strong: "Strong fit — send it",
  "worth-applying": "Worth applying",
  weak: "Weak — fix before sending",
  "no-go": "Likely auto-reject",
};

const STATE_STYLE: Record<string, string> = {
  evidenced: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  mentioned: "bg-amber-50 text-amber-800 ring-amber-200",
  missing: "bg-rose-50 text-rose-700 ring-rose-200",
};

function pct(n: number | undefined): string {
  return n == null ? "—" : `${Math.round(n * 100)}%`;
}

function SkillPills({ rows }: { rows: SkillEvidence[] }) {
  if (!rows?.length) return <span className="text-slate-400">—</span>;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {rows.map((r) => (
        <span
          key={r.skill}
          title={
            r.state === "evidenced"
              ? "Proven inside a project/experience bullet"
              : r.state === "mentioned"
              ? "Only in the skills line — no achievement backs it up"
              : "Not on the CV at all"
          }
          className={`rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ${
            STATE_STYLE[r.state] || ""
          }`}
        >
          {r.skill}
          {r.state === "mentioned" && " ·claimed"}
          {r.state === "missing" && " ·missing"}
        </span>
      ))}
    </div>
  );
}

function Meter({ label, value, good }: { label: string; value: number; good: number }) {
  const ok = value >= good;
  return (
    <div>
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="text-slate-600">{label}</span>
        <span className={ok ? "font-semibold text-emerald-700" : "font-semibold text-amber-700"}>
          {pct(value)}
        </span>
      </div>
      <div className="mt-0.5 h-1.5 w-full rounded-full bg-slate-200">
        <div
          className={`h-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`}
          style={{ width: `${Math.min(100, Math.round(value * 100))}%` }}
        />
      </div>
    </div>
  );
}

export default function ScorecardPanel({ card }: { card: Scorecard }) {
  if (!card || card.fit_score < 0) return null;
  const cov = card.coverage;
  const b = card.bullets;
  const scan = card.recruiter_scan;
  const gates = card.hard_gates || [];

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-3">
      {/* Verdict header */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-800">Recruiter scorecard</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${
            VERDICT_STYLE[card.verdict] || "bg-slate-100 text-slate-700 ring-slate-300"
          }`}
        >
          {VERDICT_LABEL[card.verdict] || card.verdict}
        </span>
        <span className="ml-auto text-xs text-slate-500">
          Fit <span className="text-base font-bold text-slate-900">{card.fit_score}</span>/100
        </span>
      </div>

      {/* Hard gates — the things that cause an instant reject */}
      {gates.length > 0 && (
        <div className="space-y-1 rounded border border-rose-300 bg-rose-50 p-2 text-xs">
          <p className="font-semibold text-rose-900">⚠ Hard requirements not met</p>
          {gates.map((g, i) => (
            <p key={i} className="text-rose-800">
              <span className="font-medium">{g.requirement}</span> — {g.detail}
            </p>
          ))}
        </div>
      )}

      {/* 7-second recruiter scan */}
      {scan && (
        <div
          className={`space-y-1 rounded border p-2 text-xs ${
            scan.verdict === "read-on"
              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
              : scan.verdict === "bin"
              ? "border-rose-300 bg-rose-50 text-rose-900"
              : "border-amber-300 bg-amber-50 text-amber-900"
          }`}
        >
          <p className="font-semibold">
            7-second scan: {scan.verdict}
            {scan.first_impression_role && (
              <span className="ml-1 font-normal opacity-80">
                · reads as “{scan.first_impression_role}”
              </span>
            )}
          </p>
          <p>{scan.reason}</p>
          {scan.fixes?.length > 0 && (
            <ul className="ml-4 list-disc">
              {scan.fixes.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Evidence-graded coverage */}
      {cov && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-3">
            <Meter label="Required · evidenced" value={cov.required_evidenced} good={0.8} />
            <Meter label="Preferred · evidenced" value={cov.preferred_evidenced} good={0.5} />
          </div>
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Required skills
            </p>
            <SkillPills rows={cov.required} />
          </div>
          {cov.preferred?.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Preferred / stack
              </p>
              <SkillPills rows={cov.preferred} />
            </div>
          )}
          {(cov.required_unevidenced?.length > 0 ||
            cov.required_missing?.length > 0) && (
            <EvidenceGap
              unevidenced={cov.required_unevidenced || []}
              missing={cov.required_missing || []}
            />
          )}
        </div>
      )}

      {/* Bullet health */}
      {b && b.total > 0 && (
        <div className="grid grid-cols-2 gap-3 border-t border-slate-100 pt-2">
          <Meter label="Bullets with a real number" value={b.metric_density} good={0.4} />
          <Meter label="Strong action-verb openers" value={b.strong_verb_ratio} good={0.8} />
          {b.hedge_count > 0 && (
            <p className="col-span-2 text-[11px] text-amber-800">
              {b.hedge_count} bullet{b.hedge_count > 1 ? "s" : ""} use hedge language
              {b.hedges?.[0] && <> (e.g. “{b.hedges[0].phrase}”)</>} — rewrite as owned actions.
            </p>
          )}
        </div>
      )}

      {/* Seniority calibration */}
      {card.seniority?.jd_level && (
        <p
          className={`rounded px-2 py-1 text-[11px] ${
            card.seniority.aligned
              ? "bg-slate-50 text-slate-600"
              : "bg-amber-50 text-amber-800"
          }`}
        >
          Seniority: JD asks <b>{card.seniority.jd_level}</b>, CV reads as{" "}
          <b>{card.seniority.cv_reads_as}</b> — {card.seniority.note}
        </p>
      )}
    </div>
  );
}


/** Turns "claimed but unproven" into real, evidenced experience.
 *  Asks where the candidate actually used each skill and writes their
 *  answer into the MASTER CV, so every future tailored CV can evidence
 *  it truthfully. Nothing is written from a "no" answer. */
function EvidenceGap({
  unevidenced,
  missing,
}: {
  unevidenced: string[];
  missing: string[];
}) {
  const [open, setOpen] = useState(false);
  const [qs, setQs] = useState<EvidenceQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<
    { skill: string; entry: string; bullet: string }[] | null
  >(null);
  const [note, setNote] = useState<string | null>(null);

  const all = [...unevidenced, ...missing];

  async function load() {
    setOpen(true);
    setBusy(true);
    setNote(null);
    try {
      const got = await fetchEvidenceQuestions(all);
      setQs(got);
      if (got.length === 0) setNote("Nothing left to ask — all of these are already answered.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not load questions.");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    const payload = qs
      .filter((q) => (answers[q.key] || "").trim())
      .map((q) => ({ ...q, answer: answers[q.key].trim() }));
    if (payload.length === 0) {
      setNote("Answer at least one — write \"no\" for anything you have not actually used.");
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const res = await applyEvidenceAnswers(payload);
      setDone(res.written);
      const bits: string[] = [];
      if (res.written.length) bits.push(`${res.written.length} added to your master CV`);
      if (res.declined.length) bits.push(`${res.declined.length} marked as "not used"`);
      if (res.skipped.length) bits.push(`${res.skipped.length} skipped`);
      setNote(bits.join(" · ") + " — re-render to see them evidenced.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="rounded bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900">
        {unevidenced.length > 0 && (
          <p>
            Claimed but unproven: <b>{unevidenced.join(", ")}</b> — a recruiter looks for
            these in your bullets and won&apos;t find them.
          </p>
        )}
        {missing.length > 0 && (
          <p className="mt-0.5">
            Not on the CV at all: <b>{missing.join(", ")}</b>.
          </p>
        )}
        <button
          type="button"
          onClick={load}
          className="mt-1 rounded bg-amber-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-amber-700"
        >
          Add real evidence for these →
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-2 text-[11px]">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-amber-900">Add real evidence</span>
        <button type="button" onClick={() => setOpen(false)} className="text-slate-500 hover:text-slate-800">✕</button>
      </div>
      <p className="text-amber-800">
        Say where you actually used each one — the project or job, and what you built.
        It gets written into your <b>master CV</b>, so it counts on every future
        application, not just this one. Write <b>“no”</b> for anything you have not used
        hands-on and it will never be asked again.
      </p>
      {busy && qs.length === 0 && <p className="text-slate-500">Loading…</p>}
      {qs.map((q) => (
        <label key={q.key} className="block">
          <span className="font-medium text-slate-700">{q.question}</span>
          <input
            type="text"
            value={answers[q.key] || ""}
            onChange={(e) => setAnswers((a) => ({ ...a, [q.key]: e.target.value }))}
            placeholder="e.g. Used n8n in the NSP workflow to route parsed enquiries into the CRM"
            className="mt-1 w-full rounded border border-amber-300 px-2 py-1"
          />
        </label>
      ))}
      {done && done.length > 0 && (
        <div className="rounded bg-white p-2">
          <p className="font-semibold text-emerald-800">Added to your master CV:</p>
          {done.map((d, i) => (
            <p key={i} className="mt-1 text-slate-700">
              <b>{d.entry}</b> — {d.bullet}
            </p>
          ))}
        </div>
      )}
      {note && <p className="text-amber-900">{note}</p>}
      {qs.length > 0 && (
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="rounded bg-amber-600 px-3 py-1.5 font-semibold text-white hover:bg-amber-700 disabled:opacity-60"
        >
          {busy ? "Writing…" : "Save to master CV"}
        </button>
      )}
    </div>
  );
}
