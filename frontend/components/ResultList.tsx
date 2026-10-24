"use client";

import { formatBytes, type Result } from "@/lib/api";
import { LicenseBadge } from "./LicenseBadge";

interface Props {
  results: Result[];
  selected: Set<number>;
  downloaded: Map<number, string>;
  onToggle: (id: number) => void;
}

export function ResultList({ results, selected, downloaded, onToggle }: Props) {
  if (results.length === 0) {
    return <div className="panel p-6 text-center text-sm muted">No results yet. Run a search from the plan.</div>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {results.map((r) => {
        const status = downloaded.get(r.id);
        return (
          <li key={r.id} className={`panel p-3 ${selected.has(r.id) ? "border-[var(--accent)]" : ""}`}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1 accent-[var(--accent)]"
                checked={selected.has(r.id)}
                disabled={status === "done" || status === "downloading" || status === "queued"}
                onChange={() => onToggle(r.id)}
                aria-label={`Select ${r.title}`}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <a href={r.url} target="_blank" rel="noreferrer" className="mono text-sm font-medium hover:underline break-all">
                    {r.title}
                  </a>
                  <span className="rounded bg-zinc-500/20 px-1 text-[11px] mono">{r.source}</span>
                  <span className="text-[11px] muted">{r.kind}</span>
                  <LicenseBadge cls={r.license_class} license={r.license} />
                  {r.meta.gated && <span className="text-[11px] text-amber-500">gated</span>}
                  {status && <span className="text-[11px] text-sky-400">{status}</span>}
                </div>
                {r.description && <p className="mt-1 line-clamp-2 text-xs muted">{r.description}</p>}
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] sm:grid-cols-4">
                  <Meter label="relevance" value={r.relevance} />
                  <Meter label="quality" value={r.quality} />
                  <span className="muted">
                    size <span className="mono">{formatBytes(r.size_bytes)}</span>
                    {r.format && <> · <span className="mono">{r.format}</span></>}
                  </span>
                  <span className="muted">
                    {r.meta.downloads != null && <>↓{r.meta.downloads.toLocaleString()} </>}
                    {r.meta.likes != null && <>★{r.meta.likes.toLocaleString()} </>}
                    {r.updated_at && <>· {new Date(r.updated_at).toLocaleDateString()}</>}
                  </span>
                </div>
                {r.meta.possible_mirrors?.length ? (
                  <div className="mt-1 text-[11px] text-amber-500">
                    possible duplicate of {r.meta.possible_mirrors.join(", ")}
                  </div>
                ) : null}
              </div>
              <div className="mono text-lg tabular-nums" title="combined score">
                {Math.round(r.score * 100)}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-1">
      <span className="muted w-14">{label}</span>
      <div className="h-1.5 flex-1 rounded bg-zinc-500/20">
        <div className="h-1.5 rounded bg-[var(--accent)]" style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
    </div>
  );
}
