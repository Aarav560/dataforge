"use client";
import {
  addEdge, Background, BackgroundVariant, Connection, Controls, Edge, Handle, Node, NodeProps, Position, ReactFlow,
  ReactFlowProvider, useEdgesState, useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, Asset } from "@/lib/api";

interface NodeType { type: string; label: string; category: string; params: Record<string, unknown>; help: string }
type OpData = { op: string; params: Record<string, unknown> };

const CAT_COLOR: Record<string, string> = {
  source: "#2f48e0", prep: "#5b6576", tabular: "#1f7fa8", text: "#c2418f", vision: "#7a4bd6", video: "#c0392b", label: "#a8700f", safety: "#1d8556", output: "#101826",
};

const Ctx = createContext<{ types: Record<string, NodeType>; status: Record<string, { status: string; note?: string }> }>({ types: {}, status: {} });

function OpNode({ id, data }: NodeProps<Node<OpData>>) {
  const { types, status } = useContext(Ctx);
  const t = types[data.op];
  const st = status[id];
  const color = CAT_COLOR[t?.category ?? ""] ?? "#5b6576";
  return (
    <div className="px-3.5 py-2.5 w-48">
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-sm" style={{ background: color }} aria-hidden />
        <span className="text-[11px] text-ink-3">{t?.category ?? "step"}</span>
        {st?.status === "running" && <span className="ember ml-auto !w-1.5 !h-1.5" aria-label="running" />}
        {st?.status === "done" && <span className="ml-auto text-ok text-[11px]">✓</span>}
        {st?.status === "error" && <span className="ml-auto text-bad text-[11px]">failed</span>}
      </div>
      <div className="text-[13px] font-semibold mt-0.5">{t?.label ?? data.op}</div>
      {st?.note && <div className="text-[11px] text-ink-2 mt-1 leading-snug line-clamp-2" title={st.note}>{st.note}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { op: OpNode };

/** Stage 04 — build or pick a transform pipeline; each run writes a new dataset version (plan §3.5). */
export function PipelineCanvas(props: { pid: string; assets: Asset[]; nodeStatus: Record<string, { status: string; note?: string }>; onRan: () => void }) {
  return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>;
}

function Canvas({ pid, assets, nodeStatus, onRan }: { pid: string; assets: Asset[]; nodeStatus: Record<string, { status: string; note?: string }>; onRan: () => void }) {
  const [types, setTypes] = useState<Record<string, NodeType>>({});
  const [templates, setTemplates] = useState<Record<string, { label: string; graph: any }>>({});
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<OpData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [plId, setPlId] = useState<string | null>(null);
  const [template, setTemplate] = useState<string>("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api.templates().then((t) => {
      setTypes(Object.fromEntries(t.node_types.map((n: NodeType) => [n.type, n])));
      setTemplates(t.templates);
    });
    api.pipelines(pid).then((ps) => {
      if (ps[0]) { setNodes(ps[0].graph.nodes); setEdges(ps[0].graph.edges.map((e: Edge) => ({ ...e, type: "smoothstep" }))); setPlId(ps[0].id); setTemplate(ps[0].template ?? ""); }
    });
  }, [pid, setEdges, setNodes]);

  const load = (key: string) => {
    const g = templates[key]?.graph;
    if (!g) return;
    setTemplate(key);
    setNodes(structuredClone(g.nodes));
    setEdges(structuredClone(g.edges).map((e: Edge) => ({ ...e, type: "smoothstep" })));
    setPlId(null);
    setSelected(null);
  };

  const onConnect = useCallback((c: Connection) => setEdges((es) => addEdge(c, es)), [setEdges]);

  const addNode = (op: string) => {
    const id = `n${Date.now().toString(36)}`;
    const last = [...nodes].sort((a, b) => b.position.x - a.position.x)[0];
    setNodes((ns) => [...ns, { id, type: "op", position: { x: (last?.position.x ?? 0) + 240, y: last?.position.y ?? 100 }, data: { op, params: { ...(types[op]?.params ?? {}) } } }]);
    if (last) setEdges((es) => [...es, { id: `e${id}`, source: last.id, target: id }]);
    setSelected(id);
  };

  const save = async () => {
    const pl = await api.savePipeline(pid, { graph: { nodes, edges }, template: template || undefined, id: plId ?? undefined });
    setPlId(pl.id);
    return pl.id as string;
  };

  const run = async () => {
    setMsg(null);
    try {
      await api.runPipeline(await save());
      setMsg({ ok: true, text: "Running. Each step reports back on the canvas; the output becomes a new dataset version." });
      onRan();
    } catch (e) { setMsg({ ok: false, text: `The run didn't start: ${e instanceof Error ? e.message : e}` }); }
  };

  const sel = nodes.find((n) => n.id === selected);
  const setParam = (k: string, v: unknown) => setNodes((ns) => ns.map((n) => (n.id === selected ? { ...n, data: { ...n.data, params: { ...n.data.params, [k]: v } } } : n)));

  return (
    <div className="h-full flex flex-col">
      <div className="shrink-0 px-4 py-2.5 border-b border-line bg-surface flex flex-wrap items-center gap-2">
        <span className="text-[12.5px] text-ink-3 mr-1">Start from</span>
        <div className="seg overflow-x-auto max-w-full">
          {Object.entries(templates).map(([k, t]) => <button key={k} data-active={template === k} onClick={() => load(k)}>{t.label}</button>)}
        </div>
        <div className="flex-1" />
        <select className="input" value="" onChange={(e) => e.target.value && addNode(e.target.value)} aria-label="Add a step">
          <option value="">Add a step…</option>
          {Object.values(types).map((t) => <option key={t.type} value={t.type}>{t.label} ({t.category})</option>)}
        </select>
        <button className="btn" onClick={() => save().then(() => setMsg({ ok: true, text: "Saved." }))} disabled={!nodes.length}>Save</button>
        <button className="btn btn-primary" onClick={run} disabled={!nodes.length}>Run pipeline</button>
      </div>
      {msg && <div className={`shrink-0 px-4 py-2 text-[13px] border-b border-line ${msg.ok ? "text-ok bg-ok-soft" : "text-bad bg-bad-soft"}`}>{msg.text}</div>}

      <div className="flex-1 min-h-0 flex">
        <div className="flex-1 min-w-0 relative">
          {nodes.length === 0 && (
            <div className="absolute inset-0 grid place-items-center z-10 pointer-events-none">
              <p className="text-ink-2 text-center max-w-sm">Pick a starting template above, or add steps one by one. Connect steps by dragging between their handles.</p>
            </div>
          )}
          <Ctx.Provider value={{ types, status: nodeStatus }}>
            <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
              nodeTypes={nodeTypes} onNodeClick={(_, n) => setSelected(n.id)} onPaneClick={() => setSelected(null)} fitView fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
              deleteKeyCode={["Delete", "Backspace"]} defaultEdgeOptions={{ type: "smoothstep", style: { stroke: "var(--ink-3)", strokeWidth: 1.5 } }} maxZoom={1.4} proOptions={{ hideAttribution: true }}>
              <Background variant={BackgroundVariant.Dots} color="var(--line-2)" gap={18} size={1.2} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </Ctx.Provider>
        </div>

        <aside className="w-72 shrink-0 border-l border-line bg-surface overflow-auto p-4 hidden md:block">
          {!sel ? (
            <div className="space-y-3 text-[13px] text-ink-2">
              <h3 className="font-semibold text-ink">Step settings</h3>
              <p>Select a step on the canvas to change its settings.</p>
              <p className="text-ink-3">Running the pipeline never overwrites data: every run is saved as a new version you can compare against earlier ones.</p>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <h3 className="font-semibold">{types[sel.data.op]?.label}</h3>
                <p className="text-[12.5px] text-ink-2 mt-1">{types[sel.data.op]?.help}</p>
              </div>
              {sel.data.op === "source" ? (
                <AssetPicker assets={assets} value={(sel.data.params.asset_ids as string[]) ?? []} onChange={(v) => setParam("asset_ids", v)} />
              ) : Object.keys(sel.data.params).length === 0 ? (
                <p className="text-[12.5px] text-ink-3">This step has no settings.</p>
              ) : (
                Object.entries(sel.data.params).map(([k, v]) => (
                  <label key={k} className="block">
                    <span className="text-[12px] text-ink-3">{k.replaceAll("_", " ")}</span>
                    {typeof v === "boolean" ? (
                      <input type="checkbox" className="ml-2 accent-[var(--accent)]" checked={v} onChange={(e) => setParam(k, e.target.checked)} />
                    ) : (
                      <input className="input w-full mt-1" value={Array.isArray(v) ? v.join(", ") : String(v ?? "")}
                        onChange={(e) => {
                          const raw = e.target.value;
                          setParam(k, Array.isArray(v) ? raw.split(",").map((s) => s.trim()).filter(Boolean) : typeof v === "number" ? Number(raw) : raw);
                        }} />
                    )}
                  </label>
                ))
              )}
              <button className="btn btn-ghost btn-sm text-bad" onClick={() => { setNodes((ns) => ns.filter((n) => n.id !== sel.id)); setEdges((es) => es.filter((e) => e.source !== sel.id && e.target !== sel.id)); setSelected(null); }}>
                Remove step
              </button>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function AssetPicker({ assets, value, onChange }: { assets: Asset[]; value: string[]; onChange: (v: string[]) => void }) {
  const done = assets.filter((a) => a.status === "done");
  if (!done.length) return <p className="text-[12.5px] text-warn">No downloads yet. Select results in Search and download them in Gather.</p>;
  return (
    <div className="space-y-1.5">
      <p className="text-[12.5px] text-ink-2">{value.length ? `${value.length} of ${done.length} downloads` : `All ${done.length} downloads`}</p>
      {done.map((a) => (
        <label key={a.id} className="flex items-center gap-2 text-[12.5px]">
          <input type="checkbox" className="accent-[var(--accent)]" checked={value.includes(a.id)} onChange={() => onChange(value.includes(a.id) ? value.filter((x) => x !== a.id) : [...value, a.id])} />
          <span className="truncate" title={a.title}>{a.title ?? a.storage_path.split(/[\\/]/).pop()}</span>
        </label>
      ))}
    </div>
  );
}
