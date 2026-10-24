"use client";
import { useEffect, useState } from "react";
import { api, fmtBytes, fmtNum, KIND_COLOR, LICENSE_CHIP, LICENSE_LABEL, Result, SOURCE_LABEL } from "@/lib/api";

/** Slide-in detail for one result: media, sample rows (streamed, not downloaded), licence, paper data links. */
export function PreviewPane({ result, onClose, onToggle, selected }: { result: Result; onClose: () => void; onToggle: (r: Result) => void; selected: boolean }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setData(null);
    setLoading(true);
    api.preview(result.id).then((d) => live && setData(d)).catch((e) => live && setData({ preview_error: String(e) })).finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [result.id]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const m = result.meta ?? {};
  const facts: [string, string][] = [
    ["Source", SOURCE_LABEL[result.source] ?? result.source],
    ["Size", result.size_bytes ? fmtBytes(result.size_bytes) : "—"],
    ["Rows", m.rows_estimate ? `~${fmtNum(m.rows_estimate)}` : "—"],
    ["Format", result.format ?? "—"],
    ["Popularity", m.downloads != null ? `${fmtNum(m.downloads)} downloads` : m.stars != null ? `${fmtNum(m.stars)} stars` : m.citations != null ? `${fmtNum(m.citations)} citations` : m.views != null ? `${fmtNum(m.views)} views` : "—"],
    ["Updated", m.updated ? new Date(m.updated).toLocaleDateString() : m.year ? String(m.year) : "—"],
  ];

  return (
    <div className="flex flex-col min-h-full">
      <div className="sticky top-0 z-10 bg-surface border-b border-line px-5 py-3 flex items-center gap-2">
        <span className="mono text-[11px] uppercase" style={{ color: KIND_COLOR[result.kind] }}>{result.kind}</span>
        <div className="flex-1" />
        <button className={selected ? "btn" : "btn btn-primary"} onClick={() => onToggle(result)}>{selected ? "✓ Selected" : "Select"}</button>
        <button className="btn btn-ghost" onClick={onClose} aria-label="Close preview">✕</button>
      </div>

      <div className="px-5 py-4 space-y-5">
        <div>
          <h2 className="display text-[20px] font-semibold leading-snug break-words">{result.title}</h2>
          <a href={result.url} target="_blank" rel="noreferrer noopener" className="text-[12.5px] text-accent hover:underline break-all">Open on {SOURCE_LABEL[result.source] ?? result.source} ↗</a>
        </div>

        {data?.embed && <iframe className="w-full aspect-video rounded-lg border border-line" src={data.embed} title={result.title} allow="encrypted-media; picture-in-picture" allowFullScreen />}
        {!data?.embed && m.thumbnail && (result.kind === "image" || result.kind === "video") && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={m.thumbnail} alt={result.title} className="w-full rounded-lg border border-line" referrerPolicy="no-referrer" />
        )}

        <div className={`rounded-lg p-3 ${result.license_class === "local_only" ? "bg-bad-soft" : ["commercial_ok", "copyleft"].includes(result.license_class) ? "bg-ok-soft" : "bg-warn-soft"}`}>
          <div className="flex items-center gap-2">
            <span className={`pill ${LICENSE_CHIP[result.license_class]} !bg-surface`}>{LICENSE_LABEL[result.license_class]}</span>
            <span className="text-[12.5px] text-ink-2 truncate">{result.license ?? "No licence stated"}</span>
          </div>
          {m.rights && (
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-0.5 text-[12px]">
              <Right label="Commercial use" v={m.rights.commercial} />
              <Right label="Redistribution" v={m.rights.redistribution} />
              <Right label="Attribution" v={m.rights.attribution} requirement />
              <Right label="Share-alike" v={m.rights.share_alike} requirement />
            </div>
          )}
          {m.local_only && <p className="text-[12px] text-bad mt-2">YouTube media is for local training only and can&apos;t be redistributed.</p>}
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5">
          {facts.map(([k, v]) => (
            <div key={k}><dt className="text-[11.5px] text-ink-3">{k}</dt><dd className="text-[13px] font-medium truncate" title={v}>{v}</dd></div>
          ))}
        </dl>

        <ScoreBreakdown r={result} />
        {m.llm_reason && <p className="text-[12.5px] text-ink-2"><b className="text-ink">LLM check:</b> {m.llm_reason}</p>}

        {(data?.description || result.description) && (
          <div>
            <p className="eyebrow mb-1">Description</p>
            <p className="text-[13px] text-ink-2 leading-relaxed whitespace-pre-wrap">{(data?.description || result.description || "").slice(0, 1200)}</p>
          </div>
        )}

        {loading && <p className="text-[12.5px] text-ink-3">Loading a sample…</p>}
        {data?.table && <SampleTable table={data.table} />}
        {data?.preview_error && <p className="text-[12.5px] text-ink-3 break-words">No sample available — the source didn&apos;t return one. Open it on {SOURCE_LABEL[result.source] ?? result.source} to inspect.</p>}

        {(m.linked_resources?.length > 0 || m.datasets_mentioned?.length > 0) && (
          <div>
            <p className="eyebrow mb-1.5">Data this paper uses</p>
            <div className="flex flex-wrap gap-1.5 mb-1.5">{m.datasets_mentioned?.map((d: string) => <span key={d} className="pill">{d}</span>)}</div>
            {m.linked_resources?.map((l: string) => <a key={l} href={l} target="_blank" rel="noreferrer noopener" className="block text-[12.5px] text-accent truncate hover:underline">{l}</a>)}
          </div>
        )}
        {m.install && <div><p className="eyebrow mb-1">Install</p><pre className="mono text-[12px] bg-sunken border border-line rounded-lg p-2.5">{m.install}</pre></div>}
      </div>
    </div>
  );
}

function Right({ label, v, requirement }: { label: string; v: boolean | null | undefined; requirement?: boolean }) {
  const text = v == null ? "unknown" : requirement ? (v ? "required" : "not required") : v ? "allowed" : "not allowed";
  const color = v == null ? "text-warn" : requirement ? "text-ink-2" : v ? "text-ok" : "text-bad";
  return <div className="flex justify-between gap-2"><span className="text-ink-2">{label}</span><span className={`font-medium ${color}`}>{text}</span></div>;
}

function ScoreBreakdown({ r }: { r: Result }) {
  const bars: [string, number][] = [["Match", r.score], ["Relevance", r.relevance], ["Quality", r.quality]];
  return (
    <div className="space-y-1.5">
      {bars.map(([k, v]) => (
        <div key={k} className="flex items-center gap-3 text-[12px]">
          <span className="w-16 text-ink-3">{k}</span>
          <div className="flex-1 h-1.5 rounded-full bg-line overflow-hidden"><div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(3, v * 100)}%`, opacity: k === "Match" ? 1 : 0.5 }} /></div>
          <span className="mono w-7 text-right tabular-nums">{Math.round(v * 100)}</span>
        </div>
      ))}
    </div>
  );
}

function SampleTable({ table }: { table: { columns: string[]; rows: Record<string, unknown>[]; split?: string } }) {
  return (
    <div>
      <p className="eyebrow mb-1.5">Sample rows{table.split ? ` · ${table.split}` : ""} <span className="normal-case tracking-normal">— streamed, not downloaded</span></p>
      <div className="overflow-auto max-h-72 border border-line rounded-lg">
        <table className="mono text-[11px] w-full">
          <thead className="sticky top-0 bg-sunken"><tr>{table.columns.map((c) => <th key={c} className="text-left px-2 py-1.5 font-medium text-ink-2 whitespace-nowrap">{c}</th>)}</tr></thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                {table.columns.map((c) => <td key={c} className="px-2 py-1 max-w-48 truncate" title={String(fmt(r[c]))}>{fmt(r[c])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const fmt = (v: unknown) => (v == null ? "∅" : typeof v === "object" ? JSON.stringify(v).slice(0, 120) : String(v).slice(0, 200));
