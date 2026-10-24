"use client";
import { Asset, fmtBytes, KIND_COLOR, LICENSE_CHIP, LICENSE_LABEL, Result, SOURCE_LABEL } from "@/lib/api";
import { LicenseMatrix } from "./LicenseMatrix";

/** Stage 03 — review the selection, check licences, download (plan §2.5, §9). */
export function GatherView({ pid, results, assets, onToggle, onGather, gathering, onNext }: {
  pid: string; results: Result[]; assets: Asset[]; onToggle: (r: Result) => void; onGather: () => void; gathering: boolean; onNext: () => void;
}) {
  const selected = results.filter((r) => r.selected);
  const est = selected.reduce((a, r) => a + (r.size_bytes ?? 0), 0);
  const done = assets.filter((a) => a.status === "done");

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-6xl mx-auto px-6 py-8 grid xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] gap-6 items-start">
        <div className="space-y-6">
          <section className="card overflow-hidden">
            <div className="px-4 py-3 flex items-center gap-3 border-b border-line">
              <h3 className="font-semibold">Selected <span className="text-ink-3 font-normal tabular-nums">{selected.length}</span></h3>
              {est > 0 && <span className="text-[12.5px] text-ink-3">≈ {fmtBytes(est)} known size</span>}
              <button className="btn btn-primary ml-auto" onClick={onGather} disabled={!selected.length || gathering}>
                {gathering ? <><span className="ember" aria-hidden />Downloading…</> : `Download ${selected.length}`}
              </button>
            </div>
            {selected.length === 0 ? (
              <p className="p-4 text-[13px] text-ink-2">Nothing selected yet. Tick results in Search to add them here.</p>
            ) : (
              <ul className="divide-y divide-line">
                {selected.map((r) => (
                  <li key={r.id} className="px-4 py-2.5 flex items-center gap-3">
                    <span className="w-1 h-8 rounded-full shrink-0" style={{ background: KIND_COLOR[r.kind] }} aria-hidden />
                    <div className="min-w-0 flex-1">
                      <div className="text-[13.5px] font-medium truncate" title={r.title}>{r.title}</div>
                      <div className="text-[12px] text-ink-3">{SOURCE_LABEL[r.source] ?? r.source} · {r.kind}{r.size_bytes ? ` · ${fmtBytes(r.size_bytes)}` : ""}</div>
                    </div>
                    <span className={`pill ${LICENSE_CHIP[r.license_class]}`}>{LICENSE_LABEL[r.license_class]}</span>
                    <button className="btn btn-ghost btn-sm" onClick={() => onToggle(r)} aria-label={`Remove ${r.title}`}>Remove</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <LicenseMatrix pid={pid} refreshKey={selected.length} />
        </div>

        <section className="card overflow-hidden">
          <div className="px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line">
            <h3 className="font-semibold whitespace-nowrap">Downloaded <span className="text-ink-3 font-normal tabular-nums">{done.length}</span></h3>
            <span className="text-[12.5px] text-ink-3 whitespace-nowrap">{fmtBytes(done.reduce((a, x) => a + (x.size_bytes ?? 0), 0))} on disk</span>
            <button className="btn ml-auto" onClick={onNext} disabled={!done.length}>Transform →</button>
          </div>
          {assets.length === 0 ? (
            <p className="p-4 text-[13px] text-ink-2">Downloads land here, stored locally with a checksum. Kaggle files need a free Kaggle API key.</p>
          ) : (
            <ul className="divide-y divide-line">
              {assets.map((a) => (
                <li key={a.id} className="px-4 py-2.5 flex items-center gap-3">
                  <Status s={a.status} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13.5px] truncate" title={a.storage_path}>{a.title ?? a.storage_path.split(/[\\/]/).pop()}</div>
                    {a.error ? <div className="text-[12px] text-bad truncate" title={a.error}>{a.error}</div>
                      : <div className="text-[12px] text-ink-3">{SOURCE_LABEL[a.source] ?? a.source ?? "—"} · {fmtBytes(a.size_bytes)}</div>}
                  </div>
                  {a.license_class && <span className={`pill ${LICENSE_CHIP[a.license_class]}`}>{LICENSE_LABEL[a.license_class]}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function Status({ s }: { s: string }) {
  if (s === "done") return <span className="w-5 h-5 rounded-full bg-ok-soft text-ok grid place-items-center text-[11px] shrink-0" aria-label="Downloaded">✓</span>;
  if (s === "error") return <span className="w-5 h-5 rounded-full bg-bad-soft text-bad grid place-items-center text-[11px] shrink-0" aria-label="Failed">!</span>;
  return <span className="w-5 h-5 grid place-items-center shrink-0" aria-label="Downloading"><span className="ember" /></span>;
}
