interface Props {
  matched: string[];
  missing: string[];
}

export default function MissingSkills({ matched, missing }: Props) {
  return (
    <div className="space-y-3">
      <p className="section-title">Skill coverage</p>

      {matched.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-emerald-700">Matched</p>
          <div className="flex flex-wrap gap-1.5">
            {matched.map((s) => (
              <span
                key={s}
                className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {missing.length > 0 ? (
        <div>
          <p className="mb-1 text-xs font-medium text-rose-700">Missing</p>
          <div className="flex flex-wrap gap-1.5">
            {missing.map((s) => (
              <span
                key={s}
                className="rounded-full bg-rose-50 px-2.5 py-0.5 text-xs font-medium text-rose-700 ring-1 ring-rose-200"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      ) : (
        matched.length > 0 && (
          <p className="text-xs text-emerald-700">No major skill gaps.</p>
        )
      )}
    </div>
  );
}
