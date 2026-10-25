"use client";

import { useEffect, useRef } from "react";
import { useWorkspace } from "@/lib/workspace";

export const SOURCE_COLORS: Record<string, string> = {
  huggingface: "#facc15",
  kaggle: "#38bdf8",
  github: "#a78bfa",
  youtube: "#f87171",
  arxiv: "#fb923c",
  semanticscholar: "#2dd4bf",
  via_paper: "#f472b6",
  plan: "#4ade80",
  gather: "#60a5fa",
  pipeline: "#c084fc",
  export: "#34d399",
  openml: "#e879f9",
  baseline: "#fbbf24",
  zenodo: "#93c5fd",
};

const TONE: Record<string, string> = { info: "", ok: "text-[var(--accent)]", warn: "text-amber-400", error: "text-red-400" };

export function CrawlFeed() {
  const { feed, sourceHits, connected } = useWorkspace();
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = box.current;
    // Follow the tail unless the user scrolled up to read.
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) end.current?.scrollIntoView({ block: "end" });
  }, [feed]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap gap-x-3 gap-y-1 border-b border-[var(--border)] px-2 py-1 text-[11px] mono">
        <span className={connected ? "text-[var(--accent)]" : "text-amber-400"}>{connected ? "● live" : "○ offline"}</span>
        {Object.entries(sourceHits).map(([src, n]) => (
          <span key={src}>
            <span style={{ color: SOURCE_COLORS[src] }}>[{src}]</span> <span className="tabular-nums">{n}</span>
          </span>
        ))}
      </div>
      <div ref={box} className="flex-1 overflow-auto px-2 py-1 text-[11px] mono leading-5">
        {feed.length === 0 && <div className="muted">Waiting for activity… run a search.</div>}
        {feed.map((l) => (
          <div key={l.id} className={`whitespace-pre-wrap break-words ${TONE[l.tone]}`}>
            <span className="muted">{new Date(l.ts * 1000).toLocaleTimeString()}</span>{" "}
            <span style={{ color: SOURCE_COLORS[l.source] ?? "var(--muted)" }}>[{l.source}]</span> {l.text}
          </div>
        ))}
        <div ref={end} />
      </div>
    </div>
  );
}
