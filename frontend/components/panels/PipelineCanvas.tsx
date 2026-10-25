"use client";

import "@xyflow/react/dist/style.css";
import {
  addEdge,
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Graph, type Pipeline, type StepDef } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

type StepData = { type: string; params: Record<string, unknown>; label: string; source: boolean; state?: string };
type StepNode = Node<StepData, "step">;

const STATE_RING: Record<string, string> = { running: "ring-2 ring-sky-400 animate-pulse", done: "ring-1 ring-[var(--accent)]" };

function StepNodeView({ data, selected }: NodeProps<StepNode>) {
  return (
    <div className={`panel min-w-[150px] px-3 py-2 text-xs ${selected ? "border-[var(--accent)]" : ""} ${STATE_RING[data.state ?? ""] ?? ""}`}>
      {!data.source && <Handle type="target" position={Position.Left} />}
      <div className="font-semibold">{data.label}</div>
      <div className="mono text-[10px] muted">{data.type}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { step: StepNodeView };

function toFlow(graph: Graph, steps: Record<string, StepDef>): { nodes: StepNode[]; edges: Edge[] } {
  // Lay out by depth when the graph has no saved positions.
  const depth: Record<string, number> = {};
  const parents = (id: string) => graph.edges.filter((e) => e.target === id).map((e) => e.source);
  const d = (id: string, seen = new Set<string>()): number => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const ps = parents(id);
    return (depth[id] = ps.length ? Math.max(...ps.map((p) => d(p, seen))) + 1 : 0);
  };
  const rowAt: Record<number, number> = {};
  const nodes = graph.nodes.map((n) => {
    const col = d(n.id);
    const row = (rowAt[col] = (rowAt[col] ?? -1) + 1);
    return {
      id: n.id,
      type: "step" as const,
      position: graph.layout?.[n.id] ?? { x: col * 210, y: row * 100 },
      data: { type: n.type, params: n.params ?? {}, label: steps[n.type]?.label ?? n.type, source: !!steps[n.type]?.is_source },
    };
  });
  const edges = graph.edges.map((e) => ({ id: `${e.source}->${e.target}`, source: e.source, target: e.target, animated: true }));
  return { nodes, edges };
}

function toGraph(nodes: StepNode[], edges: Edge[]): Graph {
  return {
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.type, params: n.data.params })),
    edges: edges.map((e) => ({ source: e.source, target: e.target })),
    layout: Object.fromEntries(nodes.map((n) => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }])),
  };
}

export function PipelineCanvas() {
  const ws = useWorkspace();
  const { project, pipelines, stepStates } = ws;
  const [steps, setSteps] = useState<Record<string, StepDef>>({});
  const [pipelineId, setPipelineId] = useState<number | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<StepNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    api.steps().then((list) => setSteps(Object.fromEntries(list.map((s) => [s.type, s])))).catch(() => {});
  }, []);

  const pipeline: Pipeline | undefined = pipelines.find((p) => p.id === pipelineId);

  useEffect(() => {
    if (pipelineId === null && pipelines.length) setPipelineId(pipelines[pipelines.length - 1].id);
  }, [pipelines, pipelineId]);

  useEffect(() => {
    if (!pipeline || !Object.keys(steps).length) return;
    const flow = toFlow(pipeline.graph_json, steps);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    setSelectedId(null);
    setDirty(false);
    // Reload only when switching pipelines or after the catalog arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipeline?.id, pipeline?.updated_at, steps]);

  // Reflect live step progress from the event stream.
  useEffect(() => {
    setNodes((ns) => ns.map((n) => (n.data.state === stepStates[n.id] ? n : { ...n, data: { ...n.data, state: stepStates[n.id] } })));
  }, [stepStates, setNodes]);

  const onConnect = useCallback(
    (c: Connection) => {
      setEdges((es) => addEdge({ ...c, animated: true }, es));
      setDirty(true);
    },
    [setEdges],
  );

  const addStep = (type: string) => {
    const spec = steps[type];
    let i = nodes.length + 1;
    while (nodes.some((n) => n.id === `n${i}`)) i++;
    const id = `n${i}`;
    const last = nodes[nodes.length - 1];
    setNodes((ns) => [
      ...ns,
      {
        id,
        type: "step",
        position: last ? { x: last.position.x + 210, y: last.position.y } : { x: 0, y: 0 },
        data: { type, params: {}, label: spec.label, source: spec.is_source },
      },
    ]);
    if (last && !spec.is_source) setEdges((es) => addEdge({ source: last.id, target: id, sourceHandle: null, targetHandle: null, animated: true }, es));
    setSelectedId(id);
    setDirty(true);
  };

  const setParam = (key: string, value: unknown) => {
    setNodes((ns) =>
      ns.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, params: { ...n.data.params, [key]: value } } } : n)),
    );
    setDirty(true);
  };

  const removeSelected = () => {
    if (!selectedId) return;
    setNodes((ns) => ns.filter((n) => n.id !== selectedId));
    setEdges((es) => es.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
    setDirty(true);
  };

  /** Save the canvas; returns the pipeline id, or null on a validation error. */
  const save = async (): Promise<number | null> => {
    if (!project) return null;
    const graph = toGraph(nodes, edges);
    try {
      let id: number;
      if (pipeline) id = (await api.updatePipeline(pipeline.id, { graph })).id;
      else {
        id = (await api.createPipeline(project.id, { graph, name: "Custom pipeline" })).id;
        setPipelineId(id);
      }
      await ws.refresh(["pipelines"]);
      setDirty(false);
      setMessage({ tone: "ok", text: "Saved." });
      return id;
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
      return null;
    }
  };

  const run = async () => {
    const id = await save();
    if (id === null) return;
    try {
      await api.runPipeline(id);
      ws.refresh(["versions", "stats"]);
      setMessage({ tone: "ok", text: "Running… follow progress in the crawl feed." });
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
    }
  };

  const newPipeline = () => {
    setPipelineId(-1);
    setNodes([]);
    setEdges([]);
    setDirty(false);
  };

  const selected = nodes.find((n) => n.id === selectedId);
  const spec = selected ? steps[selected.data.type] : undefined;
  const byCategory = useMemo(() => {
    const groups: Record<string, StepDef[]> = {};
    Object.values(steps).forEach((s) => (groups[s.category] ??= []).push(s));
    return groups;
  }, [steps]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] p-2">
        <select className="input" value={pipelineId ?? ""} onChange={(e) => (e.target.value === "new" ? newPipeline() : setPipelineId(Number(e.target.value)))}>
          {pipelines.map((p) => (
            <option key={p.id} value={p.id}>
              #{p.id} {p.name}
            </option>
          ))}
          {pipelineId === -1 && <option value={-1}>New pipeline</option>}
          <option value="new">+ New empty pipeline</option>
        </select>
        <button className="btn" onClick={save} disabled={!dirty && pipelineId !== -1}>
          Save
        </button>
        <button className="btn btn-primary" onClick={run} disabled={nodes.length === 0}>
          Save &amp; run
        </button>
        {dirty && <span className="text-[11px] text-amber-400">unsaved</span>}
        {message && <span className={`text-[11px] ${message.tone === "ok" ? "text-[var(--accent)]" : "text-red-400"}`}>{message.text}</span>}
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="w-40 shrink-0 overflow-auto border-r border-[var(--border)] p-2 text-[11px]">
          {Object.entries(byCategory).map(([cat, list]) => (
            <div key={cat} className="mb-2">
              <div className="uppercase tracking-wide muted">{cat}</div>
              {list.map((s) => (
                <button key={s.type} className="block w-full truncate py-0.5 text-left hover:text-[var(--accent)]" title={s.description} onClick={() => addStep(s.type)}>
                  + {s.label}
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="min-w-0 flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={(c) => {
              onNodesChange(c);
              if (c.some((x) => x.type === "position" && !x.dragging)) setDirty(true);
            }}
            onEdgesChange={(c) => {
              onEdgesChange(c);
              if (c.some((x) => x.type === "remove")) setDirty(true);
            }}
            onConnect={onConnect}
            onNodeClick={(_, n) => setSelectedId(n.id)}
            onPaneClick={() => setSelectedId(null)}
            colorMode="dark"
            fitView
            fitViewOptions={{ maxZoom: 1 }}
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
        {selected && spec && (
          <div className="w-56 shrink-0 overflow-auto border-l border-[var(--border)] p-2 text-[11px]">
            <div className="font-semibold">{spec.label}</div>
            <p className="muted">{spec.description}</p>
            <div className="mt-2 flex flex-col gap-2">
              {Object.entries(spec.params).map(([k, p]) => {
                const value = selected.data.params[k] ?? p.default ?? "";
                return (
                  <label key={k} className="flex flex-col gap-0.5">
                    <span className="mono">
                      {k}
                      {p.required && <span className="text-red-400">*</span>}
                    </span>
                    {p.type === "bool" ? (
                      <input type="checkbox" className="accent-[var(--accent)] self-start" checked={!!value} onChange={(e) => setParam(k, e.target.checked)} />
                    ) : p.choices ? (
                      <select className="input" value={String(value)} onChange={(e) => setParam(k, e.target.value)}>
                        {p.choices.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        className="input mono"
                        value={Array.isArray(value) ? value.join(",") : String(value)}
                        onChange={(e) => setParam(k, e.target.value)}
                      />
                    )}
                    {p.help && <span className="muted">{p.help}</span>}
                  </label>
                );
              })}
            </div>
            <button className="btn mt-3" onClick={removeSelected}>
              Remove step
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
