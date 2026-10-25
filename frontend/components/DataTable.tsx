"use client";

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    const s = value.join("");
    return value.length > 24 ? `[${value.length}] ${s.slice(0, 24)}…` : `[${s}]`;
  }
  if (typeof value === "object") return JSON.stringify(value).slice(0, 60);
  return String(value);
}

export function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return <p className="text-xs muted">No rows.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="max-h-80 overflow-auto rounded border border-[var(--border)]">
      <table className="w-full text-[11px] mono">
        <thead className="sticky top-0 bg-[var(--panel)]">
          <tr>
            {cols.map((c) => (
              <th key={c} className="border-b border-[var(--border)] px-2 py-1 text-left font-semibold">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="odd:bg-zinc-500/5">
              {cols.map((c) => (
                <td key={c} className="whitespace-nowrap px-2 py-0.5" title={JSON.stringify(r[c])}>
                  {cell(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
