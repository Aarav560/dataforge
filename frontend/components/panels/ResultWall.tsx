"use client";

import { api, formatBytes } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { LicenseBadge } from "../LicenseBadge";
import { SOURCE_COLORS } from "./CrawlFeed";

const SOURCES = ["huggingface", "kaggle", "github", "youtube", "arxiv", "semanticscholar"];
const LICENSES: [string, string][] = [
  ["commercial", "commercial OK"],
  ["non_commercial", "non-commercial"],
  ["research_only", "research only"],
  ["local_only", "local use only"],
  ["unknown", "unknown"],
];

export function ResultWall() {
  const ws = useWorkspace();
  const { results, filters, setFilters, selected, toggleSelected, focused, setFocused, freshResults, assets, project } = ws;
  const status = new Map([...assets].reverse().map((a) => [a.result_id, a.status]));

  const download = async () => {
    if (!project || selected.size === 0) return;
    try {
      await api.download(project.id, [...selected]);
      ws.clearSelected();
      ws.refresh(["assets", "stats"]);
    } catch (e) {
      ws.setError((e as Error).message);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] p-2">
        <span className="text-xs mono">{results.length} results</span>
        <select className="input" value={filters.needId ?? ""} onChange={(e) => setFilters({ needId: e.target.value ? Number(e.target.value) : null })}>
          <option value="">all needs</option>
          {project?.needs.map((n) => (
            <option key={n.id} value={n.id}>
              {n.description.slice(0, 40)}
            </option>
          ))}
        </select>
        <select className="input" value={filters.source} onChange={(e) => setFilters({ source: e.target.value })}>
          <option value="">all sources</option>
          {SOURCES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select className="input" value={filters.license} onChange={(e) => setFilters({ license: e.target.value })}>
          <option value="">any license</option>
          {LICENSES.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <button className="btn btn-primary ml-auto" disabled={selected.size === 0} onClick={download}>
          Download {selected.size || ""}
        </button>
      </div>
      <div className="flex-1 overflow-auto p-2">
        {results.length === 0 ? (
          <div className="p-6 text-center text-sm muted">No results yet. Press Ctrl+K and run /search.</div>
        ) : (
          <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(280px,1fr))]">
            {results.map((r) => {
              const st = status.get(r.id);
              const locked = st === "done" || st === "downloading" || st === "queued";
              return (
                <div
                  key={r.id}
                  onClick={() => setFocused(r.id)}
                  className={`panel cursor-pointer p-2 transition-shadow ${focused === r.id ? "ring-1 ring-[var(--accent)]" : ""} ${
                    freshResults.has(r.id) ? "fresh" : ""
                  } ${selected.has(r.id) ? "border-[var(--accent)]" : ""}`}
                >
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-0.5 accent-[var(--accent)]"
                      checked={selected.has(r.id)}
                      disabled={locked}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => toggleSelected(r.id)}
                      aria-label={`Select ${r.title}`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] mono font-medium" title={r.title}>
                        {r.title}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px]">
                        <span className="mono" style={{ color: SOURCE_COLORS[r.source] }}>
                          {r.source}
                        </span>
                        <span className="muted">{r.kind}</span>
                        <LicenseBadge cls={r.license_class} license={r.license} />
                        {st && <span className="text-sky-400">{st}</span>}
                        {r.meta.gated && <span className="text-amber-400">gated</span>}
                      </div>
                    </div>
                    <div className="mono text-base tabular-nums text-[var(--accent)]" title="combined score">
                      {Math.round(r.score * 100)}
                    </div>
                  </div>
                  {r.meta.thumbnail && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={r.meta.thumbnail} alt="" className="mt-1 h-24 w-full rounded object-cover" loading="lazy" />
                  )}
                  {r.description && <p className="mt-1 line-clamp-2 text-[11px] muted">{r.description}</p>}
                  <div className="mt-1 flex items-center gap-2 text-[10px]">
                    <Bar label="rel" value={r.relevance} />
                    <Bar label="qual" value={r.quality} />
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[10px] muted mono">
                    <span>{formatBytes(r.size_bytes)}</span>
                    {r.format && <span>{r.format.slice(0, 24)}</span>}
                    {r.meta.downloads != null && <span>↓{r.meta.downloads.toLocaleString()}</span>}
                    {r.meta.likes != null && <span>★{r.meta.likes.toLocaleString()}</span>}
                  </div>
                  {r.meta.via_paper && <div className="mt-1 truncate text-[10px] text-pink-400">via paper: {r.meta.via_paper.title}</div>}
                  {r.meta.possible_mirrors?.length ? (
                    <div className="mt-1 truncate text-[10px] text-amber-400">mirror of {r.meta.possible_mirrors.join(", ")}</div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Bar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-1 items-center gap-1">
      <span className="w-6 muted">{label}</span>
      <div className="h-1 flex-1 rounded bg-zinc-500/20">
        <div className="h-1 rounded bg-[var(--accent)]" style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
    </div>
  );
}
