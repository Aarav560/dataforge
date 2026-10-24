"use client";
import { useEffect, useState } from "react";
import { api, fmtBytes, fmtNum } from "@/lib/api";
import { JobState } from "@/lib/useProject";

/** Quiet footer: totals, workers, quota, LLM (plan §7.9). Running work shows up in the stage rail instead. */
export function StatusBar({ pid, jobs, connected, llm }: { pid: string | null; jobs: Record<string, JobState>; connected: boolean; llm: string | null }) {
  const [s, setS] = useState<any>(null);
  const running = Object.values(jobs).filter((j) => j.status === "running").length;
  useEffect(() => {
    let live = true;
    const tick = () => api.stats(pid ?? undefined).then((x) => live && setS(x)).catch(() => {});
    tick();
    const t = setInterval(tick, running ? 2000 : 8000);
    return () => { live = false; clearInterval(t); };
  }, [pid, running]);

  return (
    <footer className="h-8 shrink-0 flex items-center gap-5 px-4 border-t border-line bg-surface text-[12px] text-ink-3 overflow-x-auto whitespace-nowrap">
      {pid && <span className="flex items-center gap-1.5"><span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-ok" : "bg-bad"}`} aria-hidden />{connected ? "Live" : "Reconnecting…"}</span>}
      <span><b className="text-ink-2 font-medium tabular-nums">{fmtNum(s?.results)}</b> results</span>
      <span><b className="text-ink-2 font-medium tabular-nums">{fmtBytes(s?.bytes_gathered)}</b> downloaded</span>
      <span><b className="text-ink-2 font-medium tabular-nums">{fmtNum(s?.rows)}</b> rows built</span>
      {s?.eta_sec != null && <span className="text-heat">ETA {Math.ceil(s.eta_sec)}s</span>}
      <span className="ml-auto">GitHub {s?.api_quota?.github ?? "…"}</span>
      <span>LLM: {llm ? <span className="text-ink-2">{llm.split(":")[0].split("/").pop()}</span> : "rules only"}</span>
      <span>Free APIs only</span>
    </footer>
  );
}
