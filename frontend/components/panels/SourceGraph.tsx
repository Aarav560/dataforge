"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { useWorkspace } from "@/lib/workspace";
import { SOURCE_COLORS } from "./CrawlFeed";

// three.js needs the browser.
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), { ssr: false });

const LICENSE_COLORS: Record<string, string> = {
  commercial: "#4ade80",
  non_commercial: "#f87171",
  research_only: "#f87171",
  local_only: "#f87171",
  unknown: "#fbbf24",
};

interface GNode {
  id: string;
  label: string;
  kind: "goal" | "need" | "source" | "result";
  color: string;
  val: number;
  resultId?: number;
}

export function SourceGraph() {
  const { project, results, freshResults, setFocused, focused } = useWorkspace();
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 400, h: 300 });

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    if (!project) return { nodes: [] as GNode[], links: [] as { source: string; target: string }[] };
    const nodes: GNode[] = [{ id: "goal", label: project.goal, kind: "goal", color: "#ffffff", val: 14 }];
    const links: { source: string; target: string }[] = [];
    for (const n of project.needs) {
      nodes.push({ id: `need:${n.id}`, label: n.description, kind: "need", color: "#4ade80", val: 7 });
      links.push({ source: "goal", target: `need:${n.id}` });
      for (const src of Object.keys(n.queries)) {
        nodes.push({ id: `src:${n.id}:${src}`, label: src, kind: "source", color: SOURCE_COLORS[src] ?? "#888", val: 3 });
        links.push({ source: `need:${n.id}`, target: `src:${n.id}:${src}` });
      }
    }
    const ids = new Set(nodes.map((n) => n.id));
    for (const r of results) {
      const parent = ids.has(`src:${r.need_id}:${r.source}`) ? `src:${r.need_id}:${r.source}` : `need:${r.need_id}`;
      if (!ids.has(parent)) continue;
      const fresh = freshResults.has(r.id);
      nodes.push({
        id: `res:${r.id}`,
        label: r.title,
        kind: "result",
        color: r.id === focused ? "#ffffff" : LICENSE_COLORS[r.license_class],
        val: (fresh ? 4 : 1) + r.score * 2,
        resultId: r.id,
      });
      links.push({ source: parent, target: `res:${r.id}` });
    }
    return { nodes, links };
  }, [project, results, freshResults, focused]);

  return (
    <div ref={wrap} className="relative h-full w-full overflow-hidden bg-black">
      {data.nodes.length > 0 && (
        <ForceGraph3D
          width={size.w}
          height={size.h}
          graphData={data}
          backgroundColor="#05070a"
          nodeLabel={(n) => `${(n as GNode).kind}: ${(n as GNode).label}`}
          nodeColor={(n) => (n as GNode).color}
          nodeVal={(n) => (n as GNode).val}
          nodeOpacity={0.9}
          linkColor={() => "rgba(120,140,160,0.35)"}
          linkDirectionalParticles={(l) => (String((l as { target: GNode | string }).target).startsWith("res:") ? 0 : 1)}
          linkDirectionalParticleSpeed={0.006}
          onNodeClick={(n) => (n as GNode).resultId && setFocused((n as GNode).resultId!)}
          cooldownTicks={120}
        />
      )}
      <div className="pointer-events-none absolute bottom-2 left-2 flex gap-3 text-[10px] mono">
        <span style={{ color: LICENSE_COLORS.commercial }}>● usable</span>
        <span style={{ color: LICENSE_COLORS.unknown }}>● check license</span>
        <span style={{ color: LICENSE_COLORS.non_commercial }}>● restricted</span>
      </div>
    </div>
  );
}
