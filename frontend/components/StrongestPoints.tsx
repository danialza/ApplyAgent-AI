interface Props {
  points: string[];
  suggestions: string[];
}

export default function StrongestPoints({ points, suggestions }: Props) {
  if (points.length === 0 && suggestions.length === 0) return null;

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <div>
        <p className="section-title">Strongest points</p>
        {points.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No matching highlights detected.</p>
        ) : (
          <ul className="mt-2 space-y-2 text-sm text-slate-700">
            {points.map((p, i) => (
              <li
                key={i}
                className="flex gap-2 rounded-md bg-emerald-50 px-3 py-2 ring-1 ring-emerald-100"
              >
                <span className="mt-0.5 text-emerald-600">✓</span>
                <span className="flex-1">{p}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="section-title">Improvement suggestions</p>
        {suggestions.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No suggestions — well aligned.</p>
        ) : (
          <ul className="mt-2 space-y-2 text-sm text-slate-700">
            {suggestions.map((s, i) => (
              <li
                key={i}
                className="flex gap-2 rounded-md bg-amber-50 px-3 py-2 ring-1 ring-amber-100"
              >
                <span className="mt-0.5 text-amber-600">→</span>
                <span className="flex-1">{s}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
