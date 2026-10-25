"""A small DAG executor for transform pipelines.

A pipeline graph is {"nodes": [{"id", "type", "params"}], "edges": [{"source", "target"}]}.
Each step receives the merged context of its parents and returns a new one.
The single sink's table becomes the dataset version.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

from .video import VideoRef


class PipelineError(Exception):
    pass


@dataclass
class Context:
    table: pa.Table | None = None
    videos: list[VideoRef] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)

    @staticmethod
    def merge(parents: list["Context"]) -> "Context":
        if len(parents) == 1:
            return parents[0]
        tables = [p.table for p in parents if p.table is not None]
        merged = pa.concat_tables(tables, promote_options="permissive") if tables else None
        return Context(
            table=merged,
            videos=[v for p in parents for v in p.videos],
            files=[f for p in parents for f in p.files],
        )


@dataclass
class Env:
    """What steps may touch outside their context."""

    asset_paths: dict[int, Path]
    work_dir: Path
    settings: Any = None
    log: Callable[[str], None] = print


@dataclass
class Param:
    type: str  # int | float | str | bool | int_list | choice
    default: Any = None
    help: str = ""
    choices: list[str] | None = None
    required: bool = False

    def coerce(self, name: str, value: Any) -> Any:
        if value is None or value == "":
            if self.required:
                raise PipelineError(f"parameter {name!r} is required")
            return self.default
        try:
            if self.type == "int":
                return int(value)
            if self.type == "float":
                return float(value)
            if self.type == "bool":
                return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
            if self.type == "int_list":
                items = value if isinstance(value, list) else str(value).split(",")
                return [int(v) for v in items if str(v).strip()]
            if self.type == "choice":
                if value not in (self.choices or []):
                    raise ValueError(f"must be one of {self.choices}")
                return value
            return str(value)
        except (TypeError, ValueError) as exc:
            raise PipelineError(f"parameter {name!r}: {exc}") from exc


@dataclass
class StepDef:
    type: str
    label: str
    category: str  # source | video | table | label | output
    description: str
    params: dict[str, Param]
    run: Callable[[Context, dict, Env], Context]
    is_source: bool = False

    def describe(self) -> dict:
        return {
            "type": self.type,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "is_source": self.is_source,
            "params": {
                k: {"type": p.type, "default": p.default, "help": p.help, "choices": p.choices, "required": p.required}
                for k, p in self.params.items()
            },
        }


REGISTRY: dict[str, StepDef] = {}


def step(type_: str, label: str, category: str, description: str, params: dict[str, Param] | None = None, source=False):
    def deco(fn):
        REGISTRY[type_] = StepDef(type_, label, category, description, params or {}, fn, source)
        return fn

    return deco


def validate(graph: dict) -> list[dict]:
    """Check the graph and return nodes in execution order."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        raise PipelineError("pipeline has no steps")
    by_id = {}
    for n in nodes:
        if n.get("id") in by_id:
            raise PipelineError(f"duplicate node id {n.get('id')!r}")
        if n.get("type") not in REGISTRY:
            raise PipelineError(f"unknown step type {n.get('type')!r}")
        by_id[n["id"]] = n
    parents: dict[str, list[str]] = {nid: [] for nid in by_id}
    children: dict[str, list[str]] = {nid: [] for nid in by_id}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s not in by_id or t not in by_id:
            raise PipelineError(f"edge {s!r} -> {t!r} references a missing node")
        parents[t].append(s)
        children[s].append(t)
    for nid, n in by_id.items():
        spec = REGISTRY[n["type"]]
        if spec.is_source and parents[nid]:
            raise PipelineError(f"{spec.label} ({nid}) is a source and can't have inputs")
        if not spec.is_source and not parents[nid]:
            raise PipelineError(f"{spec.label} ({nid}) needs an input")
        for k, v in (n.get("params") or {}).items():
            if k not in spec.params:
                raise PipelineError(f"{spec.label} ({nid}) has no parameter {k!r}")
        for k, p in spec.params.items():
            p.coerce(k, (n.get("params") or {}).get(k))
    # Kahn's algorithm.
    order, indeg = [], {nid: len(p) for nid, p in parents.items()}
    ready = [nid for nid, d in indeg.items() if d == 0]
    while ready:
        nid = ready.pop(0)
        order.append(by_id[nid])
        for c in children[nid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
    if len(order) != len(by_id):
        raise PipelineError("pipeline has a cycle")
    sinks = [nid for nid in by_id if not children[nid]]
    if len(sinks) != 1:
        raise PipelineError(f"pipeline must end in exactly one step, found {len(sinks)}")
    for n in order:
        n["_parents"] = parents[n["id"]]
    return order


def run(graph: dict, env: Env, on_step: Callable[[str, str], None] | None = None) -> Context:
    order = validate(graph)
    results: dict[str, Context] = {}
    for n in order:
        spec = REGISTRY[n["type"]]
        params = {k: p.coerce(k, (n.get("params") or {}).get(k)) for k, p in spec.params.items()}
        ctx = Context() if spec.is_source else Context.merge([results[p] for p in n["_parents"]])
        if on_step:
            on_step(n["id"], "running")
        try:
            results[n["id"]] = spec.run(ctx, params, env)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(f"{spec.label} ({n['id']}) failed: {exc}") from exc
        out = results[n["id"]]
        rows = out.table.num_rows if out.table is not None else 0
        env.log(f"{spec.label}: {rows} rows, {len(out.videos)} videos, {len(out.files)} files")
        if on_step:
            on_step(n["id"], "done")
    return results[order[-1]["id"]]


def linear(*nodes: tuple[str, dict]) -> dict:
    """Build a chain graph from (type, params) pairs."""
    graph_nodes = [{"id": f"n{i + 1}", "type": t, "params": p} for i, (t, p) in enumerate(nodes)]
    edges = [{"source": a["id"], "target": b["id"]} for a, b in zip(graph_nodes, graph_nodes[1:])]
    return {"nodes": graph_nodes, "edges": edges}
