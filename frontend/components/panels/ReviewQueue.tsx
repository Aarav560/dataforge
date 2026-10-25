"use client";

import { useCallback, useEffect, useState } from "react";
import { api, assetFileUrl, type ReviewItem, type ReviewQueue as Queue } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

export function ReviewQueue() {
  const ws = useWorkspace();
  const candidates = ws.versions.filter((v) => v.status === "done" && v.columns.some((c) => c.name === "needs_review"));
  const versionId = candidates.find((v) => v.id === ws.activeVersion)?.id ?? candidates[0]?.id;
  const [queue, setQueue] = useState<Queue | null>(null);
  const [current, setCurrent] = useState(0);
  const [history, setHistory] = useState<ReviewItem[]>([]);

  const load = useCallback(async () => {
    if (versionId === undefined) return;
    try {
      setQueue(await api.reviews(versionId, "pending", 0, 100));
      setCurrent(0);
    } catch (e) {
      ws.setError((e as Error).message);
    }
  }, [versionId, ws]);

  useEffect(() => {
    setHistory([]);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionId]);

  const item = queue?.items[current];

  const decide = useCallback(
    async (action: "accept" | "correct" | "reject", label?: string) => {
      if (!item || !queue) return;
      try {
        await api.decide(item.id, action, label);
        setHistory((h) => [...h, item]);
        const items = queue.items.filter((i) => i.id !== item.id);
        const counts: Record<string, number> = { ...queue.counts, pending: (queue.counts.pending ?? 1) - 1 };
        const done = action === "accept" ? "accepted" : action === "correct" ? "corrected" : "rejected";
        counts[done] = (counts[done] ?? 0) + 1;
        setQueue({ ...queue, items, counts });
        setCurrent((c) => Math.min(c, items.length - 1));
        if (items.length === 0) load();
      } catch (e) {
        ws.setError((e as Error).message);
      }
    },
    [item, queue, load, ws],
  );

  const undo = useCallback(async () => {
    const last = history[history.length - 1];
    if (!last) return;
    await api.decide(last.id, "reset");
    setHistory((h) => h.slice(0, -1));
    load();
  }, [history, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (!item || target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || e.metaKey || e.ctrlKey) return;
      const n = Number(e.key);
      if (n >= 1 && n <= (queue?.labels.length ?? 0)) decide(queue!.labels[n - 1] === item.proposed_label ? "accept" : "correct", queue!.labels[n - 1]);
      else if (e.key === "Enter" && item.proposed_label) decide("accept");
      else if (e.key.toLowerCase() === "x") decide("reject");
      else if (e.key.toLowerCase() === "u") undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, queue, decide, undo]);

  if (versionId === undefined) return <div className="p-4 text-sm muted">Add an Auto-label step to a pipeline to fill the review queue.</div>;
  if (!queue) return <div className="p-4 text-xs muted">Loading…</div>;
  const reviewed = (queue.counts.accepted ?? 0) + (queue.counts.corrected ?? 0) + (queue.counts.rejected ?? 0);
  const version = ws.versions.find((v) => v.id === versionId);

  const apply = async () => {
    try {
      await api.applyReviews(versionId);
      ws.refresh(["versions"]);
    } catch (e) {
      ws.setError((e as Error).message);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] p-2 text-xs">
        <span className="mono">v{version?.version}</span>
        <span className="text-sky-400">{queue.counts.pending ?? 0} pending</span>
        <span className="muted">{reviewed} reviewed</span>
        <button className="btn ml-auto" onClick={undo} disabled={history.length === 0}>Undo (U)</button>
        <button className="btn btn-primary" onClick={apply} disabled={reviewed === 0}>Apply {reviewed} → new version</button>
      </div>
      {!item ? (
        <div className="p-4 text-sm muted">Queue is empty. {reviewed > 0 && "Apply your reviews to create a new version."}</div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
          <div className="text-[11px] muted mono">
            row {item.row_index} · proposed <span className="text-[var(--text)]">{item.proposed_label ?? "none"}</span>
            {item.confidence != null && ` · confidence ${(item.confidence * 100).toFixed(0)}%`}
          </div>
          {item.payload.asset_id !== undefined && item.payload.path ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={assetFileUrl(item.payload.asset_id, item.payload.path)} alt="" className="max-h-72 self-start rounded object-contain" />
          ) : (
            <p className="whitespace-pre-wrap rounded border border-[var(--border)] p-2 text-sm">{item.payload.text}</p>
          )}
          <div className="flex flex-wrap gap-2">
            {queue.labels.map((label, i) => (
              <button
                key={label}
                className={`btn ${label === item.proposed_label ? "border-[var(--accent)] text-[var(--accent)]" : ""}`}
                onClick={() => decide(label === item.proposed_label ? "accept" : "correct", label)}
              >
                <kbd className="mono muted">{i + 1}</kbd> {label}
              </button>
            ))}
            <button className="btn text-red-400" onClick={() => decide("reject")}>
              <kbd className="mono muted">X</kbd> drop row
            </button>
          </div>
          <p className="text-[11px] muted">Number keys pick a label, Enter accepts the proposal, X drops the row, U undoes.</p>
        </div>
      )}
    </div>
  );
}
