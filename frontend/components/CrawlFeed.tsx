"use client";
import { useEffect, useRef } from "react";
import { FeedEvent, SOURCE_LABEL } from "@/lib/api";
import { JobState } from "@/lib/useProject";

const JOB_NAME: Record<string, string> = { search: "Search", gather: "Download", pipeline: "Pipeline run", synthetic: "Generation" };

/** Live activity: running jobs, per-source hit counts, and the event log (plan §7.3). */
export function ActivityDrawer({ feed, counts, jobs, onClose }: { feed: FeedEvent[]; counts: Record<string, number>; jobs: Record<string, JobState>; onClose: () => void }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [feed.length]);
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const recent = Object.values(jobs).sort((a, b) => b.started - a.started).slice(0, 4);

  return (
    <aside className="drawer-in absolute right-3 bottom-3 top-3 w-[420px] max-w-[calc(100vw-24px)] z-30 card !shadow-[var(--shadow-lg)] flex flex-col overflow-hidden" aria-label="Activity">
      <div className="px-4 py-3 border-b border-line flex items-center">
        <h3 className="font-semibold">Activity</h3>
        <button className="btn btn-ghost btn-sm ml-auto" onClick={onClose} aria-label="Close activity">✕</button>
      </div>
      {recent.length > 0 && (
        <ul className="px-4 py-3 border-b border-line space-y-2.5">
          {recent.map((j) => (
            <li key={j.id}>
              <div className="flex items-center gap-2 text-[13px]">
                {j.status === "running" ? <span className="ember" aria-hidden /> : <span className={`w-2 h-2 rounded-full ${j.status === "error" ? "bg-bad" : "bg-ok"}`} aria-hidden />}
                <span className="font-medium">{JOB_NAME[j.kind] ?? j.kind}</span>
                <span className={`ml-auto mono text-[12px] ${j.status === "error" ? "text-bad" : j.status === "running" ? "text-heat" : "text-ok"}`}>
                  {j.status === "running" ? `${Math.round(j.progress * 100)}%` : j.status === "done" ? "finished" : "failed"}
                </span>
              </div>
              {j.status === "running" && (
                <div className="h-1 mt-1.5 rounded-full bg-line overflow-hidden"><div className="h-full bg-heat transition-[width]" style={{ width: `${j.progress * 100}%` }} /></div>
              )}
              {j.message && <p className="text-[12px] text-ink-3 mt-0.5 truncate" title={j.message}>{j.message}</p>}
            </li>
          ))}
        </ul>
      )}
      {top.length > 0 && (
        <div className="px-4 py-3 border-b border-line">
          <p className="eyebrow mb-2">Hits by source</p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {top.map(([s, n]) => (
              <div key={s} className="flex items-center gap-2 text-[12.5px]">
                <span className="truncate text-ink-2">{SOURCE_LABEL[s] ?? s}</span>
                <span className="ml-auto mono tabular-nums">{n}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="flex-1 overflow-auto px-4 py-2 mono text-[11.5px] leading-relaxed" role="log" aria-live="polite">
        {feed.length === 0 && <p className="text-ink-3 font-sans text-[13px] py-2">Nothing has run yet.</p>}
        {feed.filter((e) => e.type === "log").map((e, i) => (
          <div key={i} className="flex gap-3">
            <span className="text-ink-3 shrink-0 tabular-nums">{new Date(e.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
            <span className={e.level === "error" ? "text-bad" : e.level === "warn" ? "text-warn" : "text-ink-2"}>{e.message}</span>
          </div>
        ))}
        <div ref={end} />
      </div>
    </aside>
  );
}
