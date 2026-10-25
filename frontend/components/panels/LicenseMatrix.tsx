"use client";

import { useEffect, useState } from "react";
import { api, type MatrixRow, type Rights } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

const COLS: [keyof Rights, string][] = [
  ["commercial", "commercial"],
  ["redistribution", "redistribute"],
  ["attribution", "attribution req."],
  ["share_alike", "share-alike"],
];

// Obligations read the other way round: requiring attribution isn't a blocker.
const OBLIGATION = new Set<keyof Rights>(["attribution", "share_alike"]);

function Cell({ col, value }: { col: keyof Rights; value: boolean | null }) {
  if (value === null) return <span className="text-amber-400" title="unknown, check the terms">?</span>;
  if (OBLIGATION.has(col))
    return value ? <span className="text-amber-400" title="required">req</span> : <span className="muted">no</span>;
  return value ? <span className="text-green-500">✓</span> : <span className="text-red-400">✗</span>;
}

export function LicenseMatrix() {
  const { selected, results, assets, setFocused } = useWorkspace();
  const [rows, setRows] = useState<MatrixRow[]>([]);
  // Selected results, plus anything already downloaded: those are what an export would contain.
  const downloaded = new Set(assets.filter((a) => a.status === "done").map((a) => a.result_id));
  const ids = [...new Set([...selected, ...results.filter((r) => downloaded.has(r.id)).map((r) => r.id)])];
  const key = ids.sort((a, b) => a - b).join(",");

  useEffect(() => {
    if (!key) {
      setRows([]);
      return;
    }
    api.licenseMatrix(key.split(",").map(Number)).then(setRows).catch(() => setRows([]));
  }, [key]);

  if (rows.length === 0) return <div className="p-4 text-sm muted">Select or download results to compare their usage rights.</div>;

  const blocked = rows.filter((r) => r.rights.commercial === false).length;
  const unknown = rows.filter((r) => r.rights.commercial === null).length;
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border)] p-2 text-xs">
        {blocked === 0 && unknown === 0 ? (
          <span className="text-green-500">All {rows.length} sources allow commercial use.</span>
        ) : (
          <span className={blocked ? "text-red-400" : "text-amber-400"}>
            {blocked > 0 && `${blocked} source(s) block commercial use. `}
            {unknown > 0 && `${unknown} have unknown terms.`}
          </span>
        )}
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-[var(--panel)]">
            <tr className="text-left">
              <th className="px-2 py-1">source</th>
              {COLS.map(([, l]) => (
                <th key={l} className="px-2 py-1 text-center">
                  {l}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.result_id} className="cursor-pointer odd:bg-zinc-500/5 hover:bg-zinc-500/10" onClick={() => setFocused(r.result_id)}>
                <td className="max-w-[180px] truncate px-2 py-1 mono" title={`${r.title} · ${r.license ?? "no license"}`}>
                  {r.title}
                </td>
                {COLS.map(([k]) => (
                  <td key={k} className="px-2 py-1 text-center mono">
                    <Cell col={k} value={r.rights[k]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
