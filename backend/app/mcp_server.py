"""DataForge as a Claude connector (MCP server).

Claude does the thinking in Claude Desktop, claude.ai or Claude Code (on your
own plan), and calls these tools for the data work: planning, searching,
gathering, pipelines, labeling, cards, exports and baselines.

The server talks to a running DataForge backend over its REST API.

    # stdio (Claude Desktop, Claude Code); the file has no app imports, so a path works too
    python -m app.mcp_server        # or: python /path/to/backend/app/mcp_server.py
    # HTTP, protected by a bearer token
    python -m app.mcp_server --http --port 8765 --token "$MCP_TOKEN"

Environment: DATAFORGE_URL (default http://localhost:8000) and, when the
backend has AUTH_ENABLED, DATAFORGE_TOKEN (create one with POST /api/auth/token).
"""

import argparse
import asyncio
import hmac
import io
import os
import time
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

INSTRUCTIONS = """DataForge turns an ML goal into a ready-to-train dataset.

Typical flow:
1. create_project with a plan YOU write: the ML approach and 3-6 data needs, each with a kind
   (dataset, model, environment, code, video, paper) and 1-3 short keyword queries (2-4 words,
   the words dataset authors put in titles).
2. search, then get_results to read ranked results with license classes. Prefer
   license_class "commercial" unless the user says otherwise; check license_matrix before exporting.
3. download chosen results; synthetic_advice tells you when simulation beats collection.
4. run_pipeline with a template (list_templates) or a custom graph (list_pipeline_steps).
5. For labeling, add an "auto_label" step; then get_review_queue, decide each item yourself and
   submit_labels, then apply_reviews to create a new version.
6. write_card with your own summary and known limitations, then export and train_baseline.

Long jobs wait up to `timeout` seconds and return their current state if still running; call the
matching get_* tool to check again."""

POLL = 1.5


class DataForge:
    """Thin async client for the DataForge REST API."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = (base_url or os.environ.get("DATAFORGE_URL") or "http://localhost:8000").rstrip("/")
        token = token if token is not None else os.environ.get("DATAFORGE_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=120, transport=transport)

    async def call(self, method: str, path: str, **kw) -> Any:
        resp = await self.http.request(method, f"/api{path}", **kw)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text[:300]
            # ToolError messages reach Claude verbatim, so it can see what went wrong.
            raise ToolError(f"DataForge {method} {path} failed ({resp.status_code}): {detail}")
        return None if resp.status_code == 204 else resp.json()

    async def raw(self, path: str, **kw) -> bytes:
        resp = await self.http.get(f"/api{path}", **kw)
        resp.raise_for_status()
        return resp.content

    async def wait(self, fetch, done, timeout: float):
        deadline = time.monotonic() + timeout
        value = await fetch()
        while not done(value) and time.monotonic() < deadline:
            await asyncio.sleep(POLL)
            value = await fetch()
        return value


def _compact_result(r: dict) -> dict:
    meta = r.get("meta") or {}
    out = {k: r[k] for k in ("id", "need_id", "source", "kind", "title", "url", "license", "license_class")}
    out["score"] = round(r["score"], 3)
    if r.get("description"):
        out["description"] = r["description"][:240]
    if r.get("size_bytes"):
        out["size_mb"] = round(r["size_bytes"] / 1e6, 1)
    for k in ("downloads", "likes", "possible_mirrors", "via_paper", "from_index", "from_cache"):
        if meta.get(k):
            out[k] = meta[k]
    return out


def _compact_version(v: dict) -> dict:
    out = {k: v[k] for k in ("id", "version", "status", "row_count", "error", "parent_version_id") if k in v}
    out["columns"] = [f"{c['name']}:{c['type']}" for c in v.get("columns", [])]
    stats = []
    for c in (v.get("stats") or {}).get("columns", []):
        s = {"column": c["name"], "kind": c["kind"], "nulls": c.get("nulls")}
        if c.get("top"):
            s["top"] = [(t["value"], t["count"]) for t in c["top"][:8]]
        if c["kind"] == "numeric":
            s.update({k: c.get(k) for k in ("min", "max", "mean")})
        stats.append(s)
    if stats:
        out["stats"] = stats
    if v.get("log"):
        out["log_tail"] = v["log"].splitlines()[-8:]
    return out


def build_server(client: DataForge | None = None) -> MCPServer:
    df = client or DataForge()
    mcp = MCPServer("DataForge", instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool()
    async def status() -> dict:
        """Backend health, available sources (incl. plugins) and the hub index size."""
        health, sources, index = await asyncio.gather(df.call("GET", "/health"), df.call("GET", "/sources"),
                                                      df.call("GET", "/index"))
        return {"health": health, "sources": [{k: s[k] for k in ("name", "kinds", "origin", "error")} for s in sources],
                "index_entries": sum(e["count"] for e in index["entries"])}

    @mcp.tool()
    async def list_projects() -> list[dict]:
        """Projects you can access, newest first."""
        return [{k: p[k] for k in ("id", "goal", "approach", "created_at")} for p in await df.call("GET", "/projects")]

    @mcp.tool()
    async def get_project(project_id: int) -> dict:
        """A project with its data needs, their queries per source, search status and result counts."""
        return await df.call("GET", f"/projects/{project_id}")

    @mcp.tool()
    async def create_project(
        goal: str,
        approach: Literal["supervised", "reinforcement_learning", "imitation_learning", "self_supervised", "unsupervised"] | None = None,
        approach_reason: str = "",
        needs: list[dict] | None = None,
        team_id: int | None = None,
    ) -> dict:
        """Create a project from a plan you write.

        needs: list of {"description": str, "kind": "dataset"|"model"|"environment"|"code"|"video"|"paper",
        "queries": [1-3 short keyword queries]}. Omit approach and needs to use DataForge's built-in planner.
        """
        body: dict[str, Any] = {"goal": goal, "team_id": team_id}
        if needs:
            if approach is None:
                raise ToolError("give an approach together with needs")
            body["plan"] = {"approach": approach, "approach_reason": approach_reason, "needs": needs}
        return await df.call("POST", "/projects", json=body)

    @mcp.tool()
    async def add_need(project_id: int, description: str, kind: str, queries: list[str]) -> dict:
        """Add a data need to a project."""
        return await df.call("POST", f"/projects/{project_id}/needs",
                             json={"description": description, "kind": kind, "queries": queries})

    @mcp.tool()
    async def edit_need(need_id: int, description: str | None = None, kind: str | None = None,
                        queries: list[str] | None = None) -> dict:
        """Change a need's description, kind or queries (re-search it afterwards)."""
        body = {k: v for k, v in {"description": description, "kind": kind, "queries": queries}.items() if v is not None}
        return await df.call("PATCH", f"/needs/{need_id}", json=body)

    @mcp.tool()
    async def remove_need(need_id: int) -> str:
        """Delete a need and its results."""
        await df.call("DELETE", f"/needs/{need_id}")
        return "removed"

    @mcp.tool()
    async def search(project_id: int, need_ids: list[int] | None = None, timeout: float = 120) -> dict:
        """Search every source for the project's needs (or just need_ids) and wait for them to finish."""
        await df.call("POST", f"/projects/{project_id}/search", json={"need_ids": need_ids})
        wanted = set(need_ids or [])
        project = await df.wait(
            lambda: df.call("GET", f"/projects/{project_id}"),
            lambda p: all(n["status"] != "searching" for n in p["needs"] if not wanted or n["id"] in wanted),
            timeout,
        )
        return {"needs": [{"id": n["id"], "description": n["description"], "status": n["status"],
                           "results": n["result_count"], "errors": (n["error"] or "")[:300] or None}
                          for n in project["needs"] if not wanted or n["id"] in wanted]}

    @mcp.tool()
    async def get_results(project_id: int, need_id: int | None = None, source: str | None = None,
                          license_class: str | None = None, limit: int = 25) -> list[dict]:
        """Ranked results (best first). license_class: commercial, non_commercial, research_only, local_only, unknown."""
        params = {k: v for k, v in {"need_id": need_id, "source": source, "license_class": license_class,
                                    "limit": limit}.items() if v is not None}
        return [_compact_result(r) for r in await df.call("GET", f"/projects/{project_id}/results", params=params)]

    @mcp.tool()
    async def preview_result(result_id: int) -> dict:
        """Peek at a result without downloading: sample rows, model card / README, paper abstract or video link."""
        p = await df.call("GET", f"/results/{result_id}/preview")
        if p.get("type") in ("markdown", "text"):
            p["text"] = p["text"][:6000]
        if p.get("type") == "table":
            p["rows"] = p["rows"][:10]
        return p

    @mcp.tool()
    async def license_matrix(result_ids: list[int]) -> list[dict]:
        """Usage rights per result: commercial, redistribution, attribution, share-alike (null = unknown)."""
        return await df.call("POST", "/license-matrix", json={"result_ids": result_ids})

    @mcp.tool()
    async def download(project_id: int, result_ids: list[int], timeout: float = 300) -> list[dict]:
        """Download results; waits for them (up to timeout) and returns asset ids for pipelines."""
        assets = await df.call("POST", f"/projects/{project_id}/downloads", json={"result_ids": result_ids})
        ids = {a["id"] for a in assets}
        final = await df.wait(
            lambda: df.call("GET", f"/projects/{project_id}/assets"),
            lambda xs: all(a["status"] in ("done", "error") for a in xs if a["id"] in ids),
            timeout,
        )
        return [{k: a[k] for k in ("id", "result_id", "title", "status", "bytes_downloaded", "error")}
                for a in final if a["id"] in ids]

    @mcp.tool()
    async def list_assets(project_id: int) -> list[dict]:
        """Downloaded assets of a project."""
        return [{k: a[k] for k in ("id", "result_id", "title", "source", "status", "bytes_downloaded", "error")}
                for a in await df.call("GET", f"/projects/{project_id}/assets")]

    @mcp.tool()
    async def synthetic_advice(project_id: int) -> dict:
        """Whether simulating/generating data beats collecting it, with a built-in generator or a scaffold script."""
        return await df.call("GET", f"/projects/{project_id}/synthetic")

    @mcp.tool()
    async def list_templates() -> list[dict]:
        """Ready-made pipelines (pass the id to run_pipeline)."""
        return await df.call("GET", "/pipeline-templates")

    @mcp.tool()
    async def list_pipeline_steps() -> list[dict]:
        """Every pipeline step type with its parameters, for building custom graphs."""
        return await df.call("GET", "/pipeline-steps")

    @mcp.tool()
    async def run_pipeline(project_id: int, template: str | None = None, graph: dict | None = None,
                           asset_ids: list[int] | None = None, name: str | None = None, timeout: float = 300) -> dict:
        """Create and run a pipeline; returns the resulting dataset version.

        Either a template id (with asset_ids if it needs downloads) or a graph:
        {"nodes": [{"id": "n1", "type": "...", "params": {...}}], "edges": [{"source": "n1", "target": "n2"}]}.
        """
        body = {"template": template, "graph": graph, "asset_ids": asset_ids or [], "name": name}
        pipe = await df.call("POST", f"/projects/{project_id}/pipelines", json=body)
        version = await df.call("POST", f"/pipelines/{pipe['id']}/run")
        final = await df.wait(lambda: df.call("GET", f"/versions/{version['id']}"),
                              lambda v: v["status"] != "running", timeout)
        return {"pipeline_id": pipe["id"], **_compact_version(final)}

    @mcp.tool()
    async def rerun_pipeline(pipeline_id: int, graph: dict | None = None, timeout: float = 300) -> dict:
        """Run an existing pipeline again, optionally with an edited graph; creates a new version."""
        if graph is not None:
            await df.call("PUT", f"/pipelines/{pipeline_id}", json={"graph": graph})
        version = await df.call("POST", f"/pipelines/{pipeline_id}/run")
        final = await df.wait(lambda: df.call("GET", f"/versions/{version['id']}"),
                              lambda v: v["status"] != "running", timeout)
        return _compact_version(final)

    @mcp.tool()
    async def list_versions(project_id: int) -> list[dict]:
        """Dataset versions of a project, newest first."""
        return [{k: v[k] for k in ("id", "version", "status", "row_count", "parent_version_id", "created_at")}
                for v in await df.call("GET", f"/projects/{project_id}/versions")]

    @mcp.tool()
    async def get_version(version_id: int) -> dict:
        """A version's schema, column statistics (class balance, ranges) and recent log."""
        return _compact_version(await df.call("GET", f"/versions/{version_id}"))

    @mcp.tool()
    async def get_rows(version_id: int, offset: int = 0, limit: int = 20) -> dict:
        """Rows of a version (long vectors are shortened)."""
        page = await df.call("GET", f"/versions/{version_id}/rows", params={"offset": offset, "limit": min(limit, 100)})
        for row in page["rows"]:
            for k, v in row.items():
                if isinstance(v, list) and len(v) > 24:
                    row[k] = f"[{len(v)} values]"
                elif isinstance(v, str) and len(v) > 500:
                    row[k] = v[:500] + "…"
        return page

    @mcp.tool()
    async def diff_versions(version_id: int, against: int | None = None) -> dict:
        """Rows and columns added/removed versus another version (default: parent or previous)."""
        params = {"against": against} if against else None
        return await df.call("GET", f"/versions/{version_id}/diff", params=params)

    @mcp.tool()
    async def get_review_queue(version_id: int, limit: int = 10) -> list:
        """Rows waiting for a label decision. Image rows are returned as images so you can look at them.

        Decide each item and pass the decisions to submit_labels.
        """
        queue = await df.call("GET", f"/versions/{version_id}/reviews", params={"limit": min(limit, 50)})
        out: list[Any] = [{"labels": queue["labels"], "counts": queue["counts"],
                           "items": [{k: i[k] for k in ("id", "row_index", "proposed_label", "confidence")}
                                     | ({"text": i["payload"]["text"]} if "text" in i["payload"] else {})
                                     for i in queue["items"]]}]
        for item in queue["items"]:
            payload = item["payload"]
            if payload.get("asset_id") is not None and payload.get("path"):
                try:
                    data = await df.raw(f"/assets/{payload['asset_id']}/file", params={"path": payload["path"]})
                    out.append(f"image for review item {item['id']}:")
                    out.append(Image(data=_thumbnail(data), format="jpeg"))
                except httpx.HTTPError as exc:
                    out.append(f"image for review item {item['id']} unavailable: {exc}")
        return out

    @mcp.tool()
    async def submit_labels(decisions: list[dict]) -> dict:
        """Record review decisions.

        decisions: [{"review_id": int, "label": str}] to set a label (accepting the proposal if it matches),
        or [{"review_id": int, "reject": true}] to drop the row from the next version.
        """
        done, errors = 0, []
        for d in decisions:
            try:
                if d.get("reject"):
                    await df.call("POST", f"/reviews/{d['review_id']}", json={"action": "reject"})
                else:
                    await df.call("POST", f"/reviews/{d['review_id']}", json={"action": "correct", "label": d["label"]})
                done += 1
            except (ToolError, KeyError) as exc:
                errors.append(f"{d.get('review_id')}: {exc}")
        return {"recorded": done, "errors": errors}

    @mcp.tool()
    async def apply_reviews(version_id: int, timeout: float = 300) -> dict:
        """Create a new version with the recorded label decisions applied."""
        project_id = (await df.call("GET", f"/versions/{version_id}"))["project_id"]
        known = {v["id"] for v in await df.call("GET", f"/projects/{project_id}/versions")}
        await df.call("POST", f"/versions/{version_id}/apply-reviews")

        async def newest():
            versions = await df.call("GET", f"/projects/{project_id}/versions")
            return next((v for v in versions if v["id"] not in known), None)

        child = await df.wait(newest, lambda v: v is not None and v["status"] != "running", timeout)
        return _compact_version(child) if child else {"status": "queued"}

    @mcp.tool()
    async def write_card(version_id: int, summary: str, limitations: list[str] | None = None) -> str:
        """Set the dataset card's summary and known limitations (DataForge adds sources, licenses, schema, stats)."""
        v = await df.call("PUT", f"/versions/{version_id}/card", json={"summary": summary, "limitations": limitations or []})
        return v["card_md"]

    @mcp.tool()
    async def get_card(version_id: int) -> str:
        """The dataset card (Markdown) of a version."""
        return (await df.call("GET", f"/versions/{version_id}"))["card_md"] or ""

    @mcp.tool()
    async def export(version_id: int, format: Literal["parquet", "csv", "jsonl", "hf", "coco", "yolo", "tfrecord"],
                     push_to_hub: bool = False, repo_id: str | None = None, private: bool = True,
                     acknowledge_unknown_licenses: bool = False, timeout: float = 300) -> dict:
        """Export a version. Publishing to the Hugging Face Hub is blocked when source licenses don't allow it."""
        check = await df.call("GET", f"/versions/{version_id}/export-check",
                              params={"format": format, "push": push_to_hub, "acknowledge": acknowledge_unknown_licenses})
        if not check["allowed"]:
            return {"status": "blocked", **check}
        job = await df.call("POST", f"/versions/{version_id}/exports", json={
            "format": format, "push_to_hub": push_to_hub, "repo_id": repo_id, "private": private,
            "acknowledge_unknown_licenses": acknowledge_unknown_licenses})
        final = await df.wait(
            lambda: df.call("GET", f"/versions/{version_id}/exports"),
            lambda xs: next(x for x in xs if x["id"] == job["id"])["status"] in ("done", "error"),
            timeout,
        )
        e = next(x for x in final if x["id"] == job["id"])
        return {**{k: e[k] for k in ("id", "format", "status", "files", "hub_url", "warnings", "error")},
                "download_url": f"{df.base_url}/api/exports/{e['id']}/download" if e["status"] == "done" else None}

    @mcp.tool()
    async def train_baseline(version_id: int, target: str | None = None, exclude: list[str] | None = None,
                             timeout: float = 300) -> dict:
        """Train a quick model to check the dataset carries signal; compares against a trivial predictor.

        exclude=None applies suggested exclusions for columns that would leak the answer.
        """
        if exclude is None:
            exclude = (await df.call("GET", f"/versions/{version_id}/baseline-options",
                                     params={"target": target} if target else None))["suggested_exclude"]
        run = await df.call("POST", f"/versions/{version_id}/baselines", json={"target": target, "exclude": exclude})
        final = await df.wait(
            lambda: df.call("GET", f"/versions/{version_id}/baselines"),
            lambda xs: next(x for x in xs if x["id"] == run["id"])["status"] in ("done", "error"),
            timeout,
        )
        r = next(x for x in final if x["id"] == run["id"])
        res = r["result"]
        return {"status": r["status"], "error": r["error"], "target": r["target"],
                **{k: res.get(k) for k in ("task", "model", "split", "rows", "metrics", "features", "excluded")}}

    return mcp


def _thumbnail(data: bytes, side: int = 512) -> bytes:
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((side, side))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85)
        return out.getvalue()


def http_app(token: str, host: str = "127.0.0.1", allowed_hosts: list[str] | None = None):
    """The streamable-HTTP app, refusing requests without the bearer token.

    allowed_hosts: public hostnames (e.g. behind a tunnel or reverse proxy) to accept in the Host
    header, on top of localhost; the SDK rejects others to prevent DNS rebinding.
    """
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse

    security = None
    if allowed_hosts:
        local = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        hosts = local + [h for name in allowed_hosts for h in (name, f"{name}:*")]
        origins = [f"{scheme}://{h}" for scheme in ("http", "https") for h in hosts]
        security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)
    inner = build_server().streamable_http_app(host=host, transport_security=security)
    expected = f"Bearer {token}".encode()

    async def app(scope, receive, send):
        if scope["type"] == "http":
            header = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(header, expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await inner(scope, receive, send)

    return app


def main() -> None:
    import logging

    # Per-request logs from httpx would flood the connector logs in Claude.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="DataForge MCP server (Claude connector)")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("MCP_TOKEN"), help="bearer token required in HTTP mode")
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="public hostname to accept (repeatable), e.g. when behind a tunnel")
    args = parser.parse_args()
    if not args.http:
        build_server().run("stdio")
        return
    if not args.token:
        parser.error("HTTP mode needs --token (or MCP_TOKEN) so the server isn't open to anyone")
    import uvicorn

    uvicorn.run(http_app(args.token, args.host, args.allowed_host), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
