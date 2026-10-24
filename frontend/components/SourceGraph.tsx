"use client";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { KIND_COLOR, Plan, Result } from "@/lib/api";

const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), { ssr: false, loading: () => <div className="p-6 text-ink-3 text-[13px]">Loading map…</div> });

interface GNode { id: string; label: string; kind: string; val: number; result?: Result }

/** 3D force graph: goal → needs → sources → results; new nodes pulse in as they're found (plan §7.4). */
export function SourceGraph({ plan, results, onPick, fresh }: { plan: Plan; results: Result[]; onPick: (r: Result) => void; fresh: Set<string> }) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 400, h: 300 });
  const fg = useRef<any>(null);

  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    const nodes: GNode[] = [{ id: "goal", label: plan.subject || plan.goal, kind: "goal", val: 14 }];
    const links: { source: string; target: string }[] = [];
    const top = [...results].filter((r) => !r.dup_of).sort((a, b) => b.score - a.score).slice(0, 160);
    for (const n of plan.needs) {
      if (n.enabled === false) continue;
      nodes.push({ id: `need:${n.id}`, label: n.description, kind: "need", val: 6 });
      links.push({ source: "goal", target: `need:${n.id}` });
    }
    const srcSeen = new Set<string>();
    for (const r of top) {
      const needNode = r.need_id ? `need:${r.need_id}` : "goal";
      const srcId = `src:${r.need_id ?? "x"}:${r.source}`;
      if (!srcSeen.has(srcId)) {
        srcSeen.add(srcId);
        nodes.push({ id: srcId, label: r.source, kind: "source", val: 3 });
        links.push({ source: nodes.some((n) => n.id === needNode) ? needNode : "goal", target: srcId });
      }
      nodes.push({ id: r.id, label: r.title, kind: r.kind, val: 1 + r.score * 4, result: r });
      links.push({ source: srcId, target: r.id });
    }
    return { nodes, links };
  }, [plan, results]);

  useEffect(() => { fg.current?.d3Force?.("charge")?.strength(-40); }, [data]);

  return (
    <div ref={box} className="h-full w-full relative">
      <ForceGraph3D
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        nodeLabel={(n: any) => `${n.kind}: ${n.label}`}
        nodeVal={(n: any) => n.val}
        nodeColor={(n: any) => (fresh.has(n.id) ? "#ea5f12" : n.kind === "source" ? "#8a93a3" : KIND_COLOR[n.kind] ?? "#8a93a3")}
        nodeOpacity={0.9}
        linkColor={() => "rgba(91,101,118,0.35)"}
        linkDirectionalParticleColor={() => "#ea5f12"}
        linkDirectionalParticles={(l: any) => (fresh.has(typeof l.target === "object" ? l.target.id : l.target) ? 3 : 0)}
        linkDirectionalParticleSpeed={0.01}
        onNodeClick={(n: any) => n.result && onPick(n.result)}
        enableNodeDrag={false}
        showNavInfo={false}
        warmupTicks={20}
        cooldownTicks={120}
      />
      <div className="absolute bottom-3 left-4 flex flex-wrap gap-3 text-[12px] pointer-events-none card px-3 py-1.5">
        {["dataset", "model", "environment", "paper", "video", "image", "code", "web"].map((k) => (
          <span key={k} style={{ color: KIND_COLOR[k] }}>● {k}</span>
        ))}
      </div>
    </div>
  );
}
