"use client";

import { useEffect, useState } from "react";
import { fetchProfileQueries } from "@/lib/api";
import type { QueryBuilderResponse, QueryTagSet } from "@/lib/types";

interface Props {
  /** Initial queries+tags. If omitted, the editor pulls them from the profile. */
  initial?: QueryBuilderResponse;
  /**
   * Called whenever the user mutates queries or tags. The parent decides
   * when to actually send the run request.
   */
  onChange: (next: { queries: string[]; tags: QueryTagSet }) => void;
  /** Optional banner-style error reporter. */
  onError?: (message: string) => void;
}

const EMPTY_TAGS: QueryTagSet = {
  roles: [],
  skills: [],
  tools: [],
  domains: [],
  platform_tags: { linkedin: [], indeed: [], general: [] },
};

const TAG_GROUPS = ["roles", "skills", "tools", "domains"] as const;
type TagGroup = (typeof TAG_GROUPS)[number];

const PLATFORM_GROUPS = ["linkedin", "indeed", "general"] as const;
type PlatformGroup = (typeof PLATFORM_GROUPS)[number];

export default function QueryTagEditor({ initial, onChange, onError }: Props) {
  const [queries, setQueries] = useState<string[]>(initial?.queries ?? []);
  const [tags, setTags] = useState<QueryTagSet>(initial?.tags ?? EMPTY_TAGS);
  const [loading, setLoading] = useState(!initial);
  const [dirty, setDirty] = useState(false);

  // Pull from profile on first mount when no initial value was supplied.
  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchProfileQueries();
        if (!cancelled) {
          setQueries(data.queries);
          setTags(data.tags ?? EMPTY_TAGS);
          // Don't fire onChange yet — these are the auto-derived defaults.
        }
      } catch (err) {
        if (!cancelled && onError) {
          onError(err instanceof Error ? err.message : "Failed to load queries.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initial, onError]);

  // Mark dirty + bubble up changes whenever the user touches anything.
  function commit(next: { queries: string[]; tags: QueryTagSet }) {
    setDirty(true);
    onChange(next);
  }

  // ---------- query mutations ----------

  function setQuery(i: number, value: string) {
    const next = queries.map((q, idx) => (idx === i ? value : q));
    setQueries(next);
    commit({ queries: next, tags });
  }
  function addQuery() {
    const next = [...queries, ""];
    setQueries(next);
    commit({ queries: next, tags });
  }
  function removeQuery(i: number) {
    const next = queries.filter((_, idx) => idx !== i);
    setQueries(next);
    commit({ queries: next, tags });
  }

  // ---------- tag mutations ----------

  function setTagGroup(group: TagGroup, items: string[]) {
    const next = { ...tags, [group]: items };
    setTags(next);
    commit({ queries, tags: next });
  }
  function setPlatformTag(group: PlatformGroup, items: string[]) {
    const next = {
      ...tags,
      platform_tags: { ...tags.platform_tags, [group]: items },
    };
    setTags(next);
    commit({ queries, tags: next });
  }

  // ---------- reset ----------

  async function resetToProfile() {
    setLoading(true);
    try {
      const data = await fetchProfileQueries();
      setQueries(data.queries);
      setTags(data.tags ?? EMPTY_TAGS);
      setDirty(false);
      // Don't bubble up overrides — clear them.
      onChange({ queries: data.queries, tags: data.tags ?? EMPTY_TAGS });
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : "Failed to reload queries.");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-500">
        Loading queries from profile…
      </p>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Edit queries &amp; tags</h3>
          <p className="text-xs text-slate-500">
            Override the auto-derived values before running the agent.
            Reset to start from the profile-derived defaults.
          </p>
        </div>
        <button
          type="button"
          onClick={resetToProfile}
          className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
        >
          {dirty ? "Reset to profile" : "Refresh from profile"}
        </button>
      </header>

      {/* Queries */}
      <section>
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Queries
        </p>
        <ul className="mt-1.5 space-y-1.5">
          {queries.map((q, i) => (
            <li key={i} className="flex items-center gap-2">
              <input
                type="text"
                value={q}
                onChange={(e) => setQuery(i, e.target.value)}
                className="block w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-sm font-mono text-slate-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
              />
              <button
                type="button"
                onClick={() => removeQuery(i)}
                title="Remove"
                className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700"
              >
                ✕
              </button>
            </li>
          ))}
          {queries.length === 0 && (
            <li className="text-xs text-slate-400">No queries yet — add one or reset.</li>
          )}
        </ul>
        <button
          type="button"
          onClick={addQuery}
          className="mt-2 inline-flex items-center gap-1 rounded-md border border-dashed border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-600 hover:border-brand-400 hover:text-brand-700"
        >
          + Add query
        </button>
      </section>

      {/* Top-level tag groups */}
      <section className="grid gap-3 sm:grid-cols-2">
        {TAG_GROUPS.map((group) => (
          <ChipEditor
            key={group}
            label={group}
            items={tags[group]}
            onChange={(items) => setTagGroup(group, items)}
          />
        ))}
      </section>

      {/* Platform tags */}
      <section>
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Platform tags
        </p>
        <div className="mt-1.5 grid gap-3 sm:grid-cols-3">
          {PLATFORM_GROUPS.map((group) => (
            <ChipEditor
              key={group}
              label={group}
              items={tags.platform_tags[group]}
              onChange={(items) => setPlatformTag(group, items)}
              compact
            />
          ))}
        </div>
      </section>

      {dirty && (
        <p className="text-xs text-amber-700">
          Edits applied — the next agent run will use these overrides.
        </p>
      )}
    </div>
  );
}

// ---------- shared chip editor ----------

function ChipEditor({
  label,
  items,
  onChange,
  compact,
}: {
  label: string;
  items: string[];
  onChange: (next: string[]) => void;
  compact?: boolean;
}) {
  const [draft, setDraft] = useState("");

  function commit() {
    const value = draft.trim();
    if (!value) return;
    if (items.some((i) => i.toLowerCase() === value.toLowerCase())) {
      setDraft("");
      return;
    }
    onChange([...items, value]);
    setDraft("");
  }

  function remove(item: string) {
    onChange(items.filter((i) => i !== item));
  }

  return (
    <div>
      <p className={`text-xs font-semibold uppercase tracking-wider text-slate-500 ${compact ? "" : ""}`}>
        {label}
      </p>
      <div className="mt-1 flex flex-wrap gap-1">
        {items.length === 0 ? (
          <span className="text-xs text-slate-400">empty</span>
        ) : (
          items.map((item) => (
            <span
              key={item}
              className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200"
            >
              {item}
              <button
                type="button"
                onClick={() => remove(item)}
                className="text-slate-400 hover:text-rose-600"
                aria-label={`Remove ${item}`}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>
      <div className="mt-1.5 flex items-center gap-1">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            }
          }}
          placeholder={`Add ${label}…`}
          className="block w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
        />
        <button
          type="button"
          onClick={commit}
          disabled={!draft.trim()}
          className="rounded-md bg-slate-900 px-2 py-1 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          +
        </button>
      </div>
    </div>
  );
}
