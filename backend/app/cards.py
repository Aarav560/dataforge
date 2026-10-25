"""Auto-generated dataset cards (Hugging Face-compatible Markdown)."""

import json
import logging
from pathlib import Path

import anthropic
import duckdb
from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import class_imbalance
from .config import Settings
from .licensing import license_rights
from .models import Asset, DatasetVersion, Project, SearchResult
from .pipelines import REGISTRY
from .pipelines.steps_web import find_pii

log = logging.getLogger(__name__)

SYNTHETIC_STEPS = {"tetris_synthetic", "arithmetic_synthetic"}


def version_sources(db: Session, graph: dict) -> list[dict]:
    """Everything a version was built from: downloaded results, crawled sites, generators."""
    out: list[dict] = []
    asset_ids = set()
    for node in graph.get("nodes", []):
        params = node.get("params") or {}
        if node["type"] in ("assets", "load_images"):
            ids = params.get("asset_ids") or []
            asset_ids.update(int(i) for i in (ids if isinstance(ids, list) else str(ids).split(",")) if str(i).strip())
        elif node["type"] == "crawl_pages":
            for url in str(params.get("urls", "")).replace(",", "\n").split():
                out.append({"kind": "web", "title": url, "url": url, "license": None, "license_class": "unknown",
                            "rights": license_rights(None)})
        elif node["type"] in SYNTHETIC_STEPS:
            out.append({"kind": "synthetic", "title": REGISTRY[node["type"]].label, "url": None,
                        "license": "generated", "license_class": "commercial",
                        "rights": {"commercial": True, "redistribution": True, "attribution": False, "share_alike": False}})
    if asset_ids:
        rows = db.execute(
            select(Asset.id, SearchResult).join(SearchResult, Asset.result_id == SearchResult.id).where(Asset.id.in_(asset_ids))
        ).all()
        for aid, r in rows:
            out.append({"kind": r.kind, "asset_id": aid, "source": r.source, "title": r.title, "url": r.url,
                        "license": r.license, "license_class": r.license_class, "rights": license_rights(r.license)})
    return out


def combined_rights(sources: list[dict]) -> dict:
    """The most restrictive combination: one 'no' blocks, unknowns make it unknown."""
    keys = ("commercial", "redistribution")
    out: dict[str, bool | None] = {}
    for k in keys:
        values = [s["rights"][k] for s in sources]
        out[k] = False if False in values else (None if None in values or not values else True)
    out["attribution_required"] = [s["title"] for s in sources if s["rights"]["attribution"]]
    out["share_alike"] = [s["title"] for s in sources if s["rights"]["share_alike"]]
    return out


def pii_sample(path: Path, limit: int = 2000) -> dict[str, int]:
    con = duckdb.connect()
    try:
        src = f"read_parquet('{str(path).replace(chr(39), chr(39) * 2)}')"
        cols = [n for n, t, *_ in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall() if t == "VARCHAR"]
        if not cols:
            return {}
        totals: dict[str, int] = {}
        sel = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
        for row in con.execute(f"SELECT {sel} FROM {src} LIMIT {limit}").fetchall():
            for value in row:
                if isinstance(value, str):
                    for k, n in find_pii(value).items():
                        totals[k] = totals.get(k, 0) + n
        return totals
    finally:
        con.close()


def _size_category(n: int) -> str:
    for limit, label in ((1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K"),
                         (1_000_000, "100K<n<1M"), (10_000_000, "1M<n<10M")):
        if n < limit:
            return label
    return "n>10M"


def facts(db: Session, version: DatasetVersion) -> dict:
    project = db.get(Project, version.project_id)
    sources = version_sources(db, version.graph_json or {})
    stats = version.stats or {}
    steps = [
        {"type": n["type"], "label": REGISTRY[n["type"]].label if n["type"] in REGISTRY else n["type"],
         "params": n.get("params") or {}}
        for n in (version.graph_json or {}).get("nodes", [])
    ]
    split_col = next((c for c in stats.get("columns", []) if c["name"] == "split"), None)
    return {
        "goal": project.goal if project else "",
        "approach": project.approach if project else None,
        "version": version.version,
        "rows": version.row_count,
        "columns": version.schema or [],
        "sources": sources,
        "rights": combined_rights(sources),
        "steps": steps,
        "splits": {t["value"]: t["count"] for t in (split_col or {}).get("top", [])},
        "imbalance": class_imbalance(stats),
        "pii": pii_sample(Path(version.storage_path)) if version.storage_path else {},
        "synthetic": any(s["kind"] == "synthetic" for s in sources),
        "video_inferred": any(s["type"] == "infer_actions" for s in steps),
    }


def default_limitations(f: dict) -> list[str]:
    out = []
    for item in f["imbalance"]:
        out.append(f"Class imbalance in `{item['column']}`: '{item['most']}' is {item['ratio']}x more common than '{item['least']}'.")
    local = [s["title"] for s in f["sources"] if s["license_class"] == "local_only"]
    if local:
        out.append(f"Built from media licensed for local use only ({', '.join(local[:5])}); do not redistribute.")
    unknown = [s["title"] for s in f["sources"] if s["license_class"] == "unknown"]
    if unknown:
        out.append(f"License terms are unknown for {len(unknown)} source(s); check them before sharing.")
    if f["synthetic"]:
        out.append("Contains simulated data: it reflects the simulator and agent policy, not human behaviour.")
    if f["video_inferred"]:
        out.append("Actions were inferred from frame differences; dropped frames or occlusion produce 'unknown' labels.")
    if f["pii"]:
        out.append(f"A sample scan found possible personal data ({', '.join(f'{k}: {v}' for k, v in f['pii'].items())}); run the PII scrub step.")
    return out


CARD_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "limitations": {"type": "array", "items": {"type": "string"}}},
    "required": ["summary", "limitations"],
    "additionalProperties": False,
}


async def llm_sections(f: dict, settings: Settings) -> tuple[str, list[str]] | None:
    if not settings.anthropic_api_key:
        return None
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    slim = {k: v for k, v in f.items() if k != "columns"} | {"columns": [c["name"] for c in f["columns"]]}
    try:
        response = await client.beta.messages.create(
            model=settings.planner_model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=(
                "You write dataset cards. From the facts given, write a 2-4 sentence summary of what the dataset "
                "contains and what it is for, and a list of known biases and limitations a practitioner should know. "
                "Only state what the facts support; keep each limitation to one sentence."
            ),
            messages=[{"role": "user", "content": json.dumps(slim, default=str)[:60000]}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": CARD_SCHEMA}},
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            return None
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
        return data["summary"], data["limitations"]
    except (anthropic.APIError, ValueError, KeyError, StopIteration) as exc:
        log.warning("card writer unavailable: %s", exc)
        return None


def _yn(v) -> str:
    return "yes" if v is True else "no" if v is False else "unknown"


def render(f: dict, summary: str | None = None, limitations: list[str] | None = None) -> str:
    licenses = sorted({s["license"] for s in f["sources"] if s["license"]})
    front = ["---", f"pretty_name: {json.dumps(f['goal'][:80] or 'DataForge dataset')}",
             f"license: {licenses[0] if len(licenses) == 1 and licenses[0] != 'generated' else 'other'}",
             f"size_categories:\n- {_size_category(f['rows'])}", "tags:\n- dataforge", "---", ""]
    lines = front + [f"# {f['goal'] or 'Dataset'} (v{f['version']})", ""]
    lines += [summary or (
        f"Dataset built with DataForge for the goal “{f['goal']}”"
        f"{' (' + f['approach'].replace('_', ' ') + ')' if f['approach'] else ''}. "
        f"It has {f['rows']:,} rows and {len(f['columns'])} columns."
    ), ""]
    lines += ["## Schema", "", "| column | type |", "|---|---|"]
    lines += [f"| `{c['name']}` | {c['type']} |" for c in f["columns"]]
    if f["splits"]:
        lines += ["", "## Splits", "", "| split | rows |", "|---|---|"]
        lines += [f"| {k} | {v:,} |" for k, v in f["splits"].items()]
    lines += ["", "## Sources", ""]
    if f["sources"]:
        lines += ["| source | license | commercial | redistribution |", "|---|---|---|---|"]
        for s in f["sources"]:
            title = f"[{s['title']}]({s['url']})" if s.get("url") else s["title"]
            lines.append(f"| {title} | {s['license'] or 'unknown'} | {_yn(s['rights']['commercial'])} | {_yn(s['rights']['redistribution'])} |")
    else:
        lines.append("No external sources recorded.")
    r = f["rights"]
    lines += ["", "## License summary", "",
              f"- Commercial use: **{_yn(r['commercial'])}**",
              f"- Redistribution: **{_yn(r['redistribution'])}**"]
    if r["attribution_required"]:
        lines.append(f"- Attribution required for: {', '.join(r['attribution_required'])}")
    if r["share_alike"]:
        lines.append(f"- Share-alike terms apply from: {', '.join(r['share_alike'])}")
    lines += ["", "## How it was built", ""]
    for i, s in enumerate(f["steps"], 1):
        params = ", ".join(f"{k}={v}" for k, v in s["params"].items() if k != "asset_ids")
        lines.append(f"{i}. {s['label']}{f' ({params})' if params else ''}")
    lines += ["", "## Known biases and limitations", ""]
    lims = limitations if limitations is not None else default_limitations(f)
    lines += [f"- {x}" for x in lims] or ["- None detected automatically. Review samples before training."]
    if r["attribution_required"]:
        lines += ["", "## Attribution", ""]
        lines += [f"- {s['title']}" + (f" — {s['url']}" if s.get("url") else "") for s in f["sources"] if s["rights"]["attribution"]]
    return "\n".join(lines) + "\n"


async def build_card(db: Session, version: DatasetVersion, settings: Settings) -> str:
    f = facts(db, version)
    sections = await llm_sections(f, settings)
    if sections:
        summary, extra = sections
        # Keep the deterministic findings and add what the writer noticed.
        lims = default_limitations(f) + [x for x in extra if x not in default_limitations(f)]
        return render(f, summary, lims)
    return render(f)
