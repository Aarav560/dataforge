"use client";
import { useMemo, useState } from "react";
import { fmtBytes, fmtNum, KIND_COLOR, KIND_LABEL, LICENSE_CHIP, LICENSE_LABEL, Plan, Result, SOURCE_LABEL } from "@/lib/api";
import { SourceGraph } from "./SourceGraph";

type Sort = "score" | "relevance" | "quality" | "size";
type Usable = "all" | "ok" | "check" | "blocked";
const USABLE: Record<Usable, (c: string) => boolean> = {
  all: () => true,
  ok: (c) => c === "commercial_ok" || c === "copyleft",
  check: (c) => ["non_commercial", "research_only", "no_derivatives", "unknown"].includes(c),
  blocked: (c) => c === "local_only",
};

/** Stage 02 — ranked, vetted results streaming in from every source (plan §3.2–3.3). */
export function SearchView({ plan, results, fresh, onToggle, onPreview, activeId, onSearch, searching, selectedCount, onReview }: {
  plan: Plan; results: Result[]; fresh: Set<string>; onToggle: (r: Result) => void; onPreview: (r: Result) => void; activeId: string | null;
  onSearch: (needIds?: string[]) => void; searching: boolean; selectedCount: number; onReview: () => void;
}) {
  const [need, setNeed] = useState<string | null>(null);
  const [kind, setKind] = useState<string>("all");
  const [usable, setUsable] = useState<Usable>("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("score");
  const [hideDupes, setHideDupes] = useState(true);
  const [view, setView] = useState<"list" | "map">("list");

  const base = useMemo(() => results.filter((r) => !hideDupes || !r.dup_of), [results, hideDupes]);
  const kindCounts = useMemo(() => count(base.filter((r) => !need || r.need_id === need), (r) => r.kind), [base, need]);
  const needCounts = useMemo(() => count(base, (r) => r.need_id ?? "generated"), [base]);

  const shown = useMemo(() => {
    const ql = q.toLowerCase();
    return base
      .filter((r) => (!need || r.need_id === need) && (kind === "all" || r.kind === kind) && USABLE[usable](r.license_class)
        && (!ql || `${r.title} ${r.description} ${r.source}`.toLowerCase().includes(ql)))
      .sort((a, b) => sort === "size" ? (b.size_bytes ?? 0) - (a.size_bytes ?? 0) : (b[sort] as number) - (a[sort] as number))
      .slice(0, 300);
  }, [base, need, kind, usable, q, sort]);

  if (!results.length && !searching) {
    return (
      <div className="h-full grid place-items-center px-6">
        <div className="text-center max-w-md">
          <h2 className="display text-[24px] font-semibold">Nothing searched yet</h2>
          <p className="text-ink-2 mt-2">Search runs every need in your plan against its sources at once. Results appear here as they arrive, ranked and licence-checked.</p>
          <button className="btn btn-primary mt-5 h-10 px-5" onClick={() => onSearch()}>Search all sources</button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex">
      <aside className="hidden md:block w-60 shrink-0 border-r border-line overflow-auto px-3 py-4 space-y-6 bg-surface">
        <Facet title="Needs">
          <FacetItem active={!need} onClick={() => setNeed(null)} label="All needs" n={base.length} />
          {plan.needs.filter((n) => n.enabled !== false).map((n) => (
            <FacetItem key={n.id} active={need === n.id} onClick={() => setNeed(need === n.id ? null : n.id)} label={n.description}
              n={needCounts[n.id] ?? 0} color={KIND_COLOR[n.kind]} hot={n.status === "searching"} />
          ))}
          {needCounts.generated ? <FacetItem active={need === "generated"} onClick={() => setNeed(null)} label="Generated" n={needCounts.generated} /> : null}
        </Facet>
        <Facet title="Licence">
          {([["all", "Any licence"], ["ok", "Usable commercially"], ["check", "Check before use"], ["blocked", "Local use only"]] as [Usable, string][]).map(([k, l]) => (
            <FacetItem key={k} active={usable === k} onClick={() => setUsable(k)} label={l} n={k === "all" ? undefined : base.filter((r) => USABLE[k](r.license_class)).length}
              color={k === "ok" ? "var(--ok)" : k === "check" ? "var(--warn)" : k === "blocked" ? "var(--bad)" : undefined} />
          ))}
        </Facet>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <div className="shrink-0 px-4 py-2.5 border-b border-line bg-surface flex flex-wrap items-center gap-2">
          <div className="seg" role="tablist" aria-label="Kind">
            <button data-active={kind === "all"} onClick={() => setKind("all")}>All</button>
            {Object.entries(kindCounts).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
              <button key={k} data-active={kind === k} onClick={() => setKind(k)}>{KIND_LABEL[k] ?? k} <span className="text-ink-3 tabular-nums">{n}</span></button>
            ))}
          </div>
          <div className="flex-1" />
          {searching && <span className="pill pill-heat"><span className="ember !w-1.5 !h-1.5" aria-hidden />Searching · {results.length} so far</span>}
          <input className="input w-44" placeholder="Filter results" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter results" />
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort by">
            <option value="score">Best match</option><option value="relevance">Relevance</option><option value="quality">Quality</option><option value="size">Size</option>
          </select>
          <label className="text-[12.5px] text-ink-2 flex items-center gap-1.5"><input type="checkbox" checked={hideDupes} onChange={() => setHideDupes(!hideDupes)} className="accent-[var(--accent)]" />Hide mirrors</label>
          <div className="seg" aria-label="View">
            <button data-active={view === "list"} onClick={() => setView("list")}>List</button>
            <button data-active={view === "map"} onClick={() => setView("map")}>Map</button>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-auto">
          {view === "map" ? (
            <SourceGraph plan={plan} results={shown} onPick={onPreview} fresh={fresh} />
          ) : shown.length === 0 ? (
            <p className="p-8 text-center text-ink-2">No results match these filters.</p>
          ) : (
            <ul className="max-w-5xl mx-auto px-4 py-3 space-y-1.5">
              {shown.map((r) => <Row key={r.id} r={r} fresh={fresh.has(r.id)} active={activeId === r.id} onToggle={onToggle} onPreview={onPreview} />)}
            </ul>
          )}
        </div>

        {selectedCount > 0 && (
          <div className="shrink-0 border-t border-line bg-surface px-4 py-2.5 flex items-center gap-3">
            <span className="text-[13.5px]"><b className="tabular-nums">{selectedCount}</b> selected</span>
            <span className="text-[12.5px] text-ink-3 hidden sm:inline">Review licences, then download.</span>
            <button className="btn btn-primary ml-auto" onClick={onReview}>Review & download →</button>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ r, fresh, active, onToggle, onPreview }: { r: Result; fresh: boolean; active: boolean; onToggle: (r: Result) => void; onPreview: (r: Result) => void }) {
  const m = r.meta ?? {};
  const pop = m.downloads ?? m.stars ?? m.citations ?? m.views;
  const popLabel = m.downloads != null ? "downloads" : m.stars != null ? "stars" : m.citations != null ? "citations" : m.views != null ? "views" : "";
  return (
    <li className={fresh ? "arrive" : undefined}>
      <div onClick={() => onPreview(r)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onPreview(r)}
        className={`group flex items-center gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors ${
          active ? "border-accent bg-accent-soft/40" : r.selected ? "border-ok/50 bg-surface" : "border-line bg-surface hover:border-line-2"}`}>
        <input type="checkbox" checked={!!r.selected} onClick={(e) => e.stopPropagation()} onChange={() => onToggle(r)}
          aria-label={`Select ${r.title}`} className="w-4 h-4 accent-[var(--accent)] shrink-0" />
        <Thumb src={m.thumbnail} kind={r.kind} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium text-[14px] truncate" title={r.title}>{r.title}</span>
            {r.dup_of && <span className="pill">mirror</span>}
          </div>
          <div className="text-[12.5px] text-ink-2 truncate">
            <span className="text-ink-3">{SOURCE_LABEL[r.source] ?? r.source}</span>
            {r.description ? <> · {r.description}</> : null}
          </div>
        </div>
        <div className="hidden lg:flex flex-col items-end gap-1 w-28 shrink-0 text-[12px] text-ink-3 tabular-nums">
          <span>{r.size_bytes ? fmtBytes(r.size_bytes) : pop != null ? `${fmtNum(pop)} ${popLabel}` : "—"}</span>
          <span>{r.format ? r.format.slice(0, 14) : ""}</span>
        </div>
        <span className={`pill ${LICENSE_CHIP[r.license_class] ?? "pill-warn"} shrink-0 hidden sm:inline-flex`} title={r.license ?? "No licence information"}>
          {LICENSE_LABEL[r.license_class] ?? r.license_class}
        </span>
        <Score r={r} />
      </div>
    </li>
  );
}

function Thumb({ src, kind }: { src?: string; kind: string }) {
  const [failed, setFailed] = useState(false);
  if (src && !failed) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} className="w-11 h-11 rounded-md object-cover border border-line shrink-0 bg-sunken" />;
  }
  return (
    <span className="w-11 h-11 rounded-md shrink-0 grid place-items-center mono text-[10px] uppercase" style={{ background: `color-mix(in srgb, ${KIND_COLOR[kind]} 10%, transparent)`, color: KIND_COLOR[kind] }}>
      {kind.slice(0, 4)}
    </span>
  );
}

function Score({ r }: { r: Result }) {
  const pct = Math.round(r.score * 100);
  return (
    <div className="w-14 shrink-0 text-right" title={`Relevance ${Math.round(r.relevance * 100)} · quality ${Math.round(r.quality * 100)}`}>
      <div className="mono text-[14px] font-medium tabular-nums">{pct}</div>
      <div className="h-1 rounded-full bg-line overflow-hidden mt-0.5">
        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(4, pct)}%` }} />
      </div>
    </div>
  );
}

function Facet({ title, children }: { title: string; children: React.ReactNode }) {
  return <div><p className="eyebrow px-2 mb-1.5">{title}</p><div className="space-y-0.5">{children}</div></div>;
}

function FacetItem({ active, onClick, label, n, color, hot }: { active: boolean; onClick: () => void; label: string; n?: number; color?: string; hot?: boolean }) {
  return (
    <button onClick={onClick} aria-pressed={active}
      className={`w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors ${active ? "bg-sunken text-ink font-medium" : "text-ink-2 hover:bg-sunken"}`}>
      {color && <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color }} aria-hidden />}
      <span className="truncate flex-1" title={label}>{label}</span>
      {hot ? <span className="ember !w-1.5 !h-1.5" aria-label="searching" /> : n != null ? <span className="mono text-[11px] text-ink-3 tabular-nums">{n}</span> : null}
    </button>
  );
}

function count<T>(xs: T[], key: (x: T) => string): Record<string, number> {
  const c: Record<string, number> = {};
  xs.forEach((x) => (c[key(x)] = (c[key(x)] ?? 0) + 1));
  return c;
}
