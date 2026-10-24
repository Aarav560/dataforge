"""Pipeline graphs (React Flow JSON) + reusable templates + runner."""
from __future__ import annotations

from collections import defaultdict, deque

from ..events import Job
from .nodes import NODE_TYPES, public_node_types, run_node


def _chain(ops: list[tuple[str, dict]]) -> dict:
    # Rows of four keep templates legible when the canvas fits them to view.
    nodes = [{"id": f"n{i}", "type": "op", "position": {"x": (i % 4) * 250, "y": (i // 4) * 150},
              "data": {"op": op, "params": {**NODE_TYPES[op]["params"], **params}}} for i, (op, params) in enumerate(ops)]
    edges = [{"id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}"} for i in range(len(ops) - 1)]
    return {"nodes": nodes, "edges": edges}


TEMPLATES = {
    "tables": {"label": "Multiple CSVs → Unified table", "graph": _chain([
        ("source", {}), ("unzip", {}), ("load_tables", {}), ("unify", {}), ("clean", {}), ("dedupe", {}), ("pii_scan", {}), ("split", {})])},
    "video_state_action": {"label": "Video → State/Action pairs", "graph": _chain([
        ("source", {}), ("extract_frames", {}), ("grid_sample", {}), ("infer_actions", {}), ("dedupe", {"subset": ["state", "action"]}), ("split", {})])},
    "web_text": {"label": "Web pages → Text corpus", "graph": _chain([
        ("source", {}), ("html_to_text", {}), ("dedupe", {"subset": ["text"]}), ("pii_scan", {}), ("chunk_text", {}), ("split", {})])},
    "images_labeled": {"label": "Images → Labeled classification set", "graph": _chain([
        ("source", {}), ("unzip", {}), ("images_to_labeled", {}), ("relabel", {}), ("dedupe", {"subset": ["image_hash"]}), ("split", {"stratify": "label"})])},
}


def topo_order(graph: dict) -> list[dict]:
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    indeg = {k: 0 for k in nodes}
    adj = defaultdict(list)
    for e in graph.get("edges", []):
        if e["source"] in nodes and e["target"] in nodes:
            adj[e["source"]].append(e["target"])
            indeg[e["target"]] += 1
    q = deque(sorted((k for k, d in indeg.items() if d == 0), key=lambda k: nodes[k]["position"]["x"] if "position" in nodes[k] else 0))
    out = []
    while q:
        k = q.popleft()
        out.append(nodes[k])
        for t in adj[k]:
            indeg[t] -= 1
            if indeg[t] == 0:
                q.append(t)
    if len(out) != len(nodes):
        raise RuntimeError("pipeline graph has a cycle")
    return out


async def run_pipeline(job: Job, project_id: str, graph: dict) -> dict:
    order = topo_order(graph)
    if not order:
        raise RuntimeError("empty pipeline")
    ctx = {"project_id": project_id, "files": [], "table": None, "notes": [], "review": []}
    if order[0]["data"]["op"] != "source":
        from .nodes import n_source
        ctx = n_source(ctx, {}, job)
    for i, n in enumerate(order):
        op = n["data"]["op"]
        if op not in NODE_TYPES:
            raise RuntimeError(f"unknown node type: {op}")
        job.progress(i / len(order), f"running {NODE_TYPES[op]['label']}")
        job.log(f"▶ {NODE_TYPES[op]['label']}")
        from ..events import bus
        bus.publish(project_id, "pipeline_node", node_id=n["id"], status="running")
        try:
            ctx = await run_node(op, ctx, n["data"].get("params") or {}, job)
        except Exception:
            bus.publish(project_id, "pipeline_node", node_id=n["id"], status="error")
            raise
        bus.publish(project_id, "pipeline_node", node_id=n["id"], status="done", note=ctx["notes"][-1] if ctx["notes"] else "")
        job.log(f"  {ctx['notes'][-1] if ctx['notes'] else 'ok'}")
    return ctx


__all__ = ["TEMPLATES", "run_pipeline", "public_node_types", "topo_order"]
