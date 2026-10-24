"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const COLS: [string, string, boolean][] = [["commercial", "Commercial", false], ["redistribution", "Redistribute", false], ["attribution", "Attribution", true], ["share_alike", "Share-alike", true]];

/** Selected sources × usage rights, with an export verdict for the chosen use (plan §7.7, §9). */
export function LicenseMatrix({ pid, refreshKey }: { pid: string; refreshKey: number }) {
  const [use, setUse] = useState<"research" | "commercial">("research");
  const [m, setM] = useState<any>(null);
  useEffect(() => { api.licenseMatrix(pid, use).then(setM).catch(() => setM(null)); }, [pid, use, refreshKey]);

  return (
    <section className="card overflow-hidden">
      <div className="px-4 py-3 flex flex-wrap items-center gap-3 border-b border-line">
        <h3 className="font-semibold">Licence check</h3>
        <div className="seg" aria-label="Intended use">
          <button data-active={use === "research"} onClick={() => setUse("research")}>Research</button>
          <button data-active={use === "commercial"} onClick={() => setUse("commercial")}>Commercial</button>
        </div>
        {m && m.items.length > 0 && (
          <span className={`ml-auto pill ${m.compatible ? "pill-ok" : "pill-bad"}`}>{m.compatible ? "Can be exported" : "Export blocked"}</span>
        )}
      </div>
      {!m ? <p className="p-4 text-ink-3 text-[13px]">Loading…</p> : m.items.length === 0 ? (
        <p className="p-4 text-ink-2 text-[13px]">Select results to see what you&apos;re allowed to do with them.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-ink-3 text-left">
                  <th className="font-normal px-4 py-2">Source</th>
                  {COLS.map(([, l]) => <th key={l} className="font-normal px-2 py-2 text-center whitespace-nowrap">{l}</th>)}
                </tr>
              </thead>
              <tbody>
                {m.items.map((it: any) => (
                  <tr key={it.id} className="border-t border-line">
                    <td className="px-4 py-2 max-w-64">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: `var(--${it.color === "green" ? "ok" : it.color === "red" ? "bad" : "warn"})` }} aria-hidden />
                        <span className="truncate" title={it.title}>{it.title}</span>
                      </div>
                      <div className="text-[11.5px] text-ink-3 truncate pl-4">{it.license ?? "no licence stated"}</div>
                    </td>
                    {COLS.map(([k, , req]) => <td key={k} className="text-center px-2"><Cell v={it.rights?.[k]} requirement={req} /></td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {m.problems.length > 0 && (
            <ul className="px-4 py-3 border-t border-line space-y-1 bg-warn-soft/60">
              {m.problems.map((p: string) => <li key={p} className="text-[12.5px] text-warn">⚠ {p}</li>)}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function Cell({ v, requirement }: { v: boolean | null | undefined; requirement?: boolean }) {
  if (v == null) return <span className="text-warn" title="Unknown">?</span>;
  if (requirement) return <span className={v ? "text-ink" : "text-ink-3"}>{v ? "Required" : "—"}</span>;
  return v ? <span className="text-ok font-medium" title="Allowed">✓</span> : <span className="text-bad font-medium" title="Not allowed">✕</span>;
}
