"""Auto-labeling with Claude (or a keyword heuristic), feeding the review queue."""

import base64
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pyarrow as pa

from .engine import Context, Env, Param, PipelineError, step

log = logging.getLogger(__name__)
MAX_IMAGE_SIDE = 512


def _image_block(path) -> dict:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def _schema(labels: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "label": {"type": "string", "enum": [*labels, "unknown"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["index", "label", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _claude_batch(client, model: str, labels: list[str], instruction: str, batch: list[tuple[int, dict]]) -> dict[int, tuple[str, float]]:
    content: list[dict] = [{
        "type": "text",
        "text": (
            f"Label each item with exactly one of: {', '.join(labels)}. Use 'unknown' if none fits. "
            "Give a confidence from 0 to 1 that reflects how sure you are."
            + (f"\nGuidelines: {instruction}" if instruction else "")
        ),
    }]
    for idx, item in batch:
        content.append({"type": "text", "text": f"Item {idx}:" + (f" {item['text']}" if "text" in item else "")})
        if "image" in item:
            content.append(item["image"])
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema(labels)}},
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"labeler stopped: {response.stop_reason}")
    data = json.loads(next(b.text for b in response.content if b.type == "text"))
    return {it["index"]: (it["label"], max(0.0, min(1.0, float(it["confidence"])))) for it in data["items"]}


def _heuristic(text: str | None, labels: list[str]) -> tuple[str, float]:
    if not text:
        return "unknown", 0.0
    lowered = text.lower()
    hits = [l for l in labels if re.search(rf"\b{re.escape(l.lower())}\b", lowered)]
    return (hits[0], 0.6) if len(hits) == 1 else ("unknown", 0.0)


@step(
    "auto_label",
    "Auto-label",
    "label",
    "Label rows with Claude (text or images), or keyword matching without an API key. Low-confidence rows go to the review queue.",
    {
        "labels": Param("str", required=True, help="Comma-separated label set"),
        "column": Param("str", "text", "Text column, or 'image' for rows from Load images"),
        "output": Param("str", "label", "Column to write labels into"),
        "instruction": Param("str", "", "Extra labeling guidelines"),
        "threshold": Param("float", 0.8, "Confidence below which rows need review"),
        "only_missing": Param("bool", True, "Keep existing labels; label only empty rows"),
        "batch_size": Param("int", 10),
        "max_rows": Param("int", 2000, "Cap on rows sent to the model per run"),
    },
)
def auto_label(ctx: Context, p: dict, env: Env) -> Context:
    t = ctx.table
    if t is None:
        raise PipelineError("Auto-label needs a table input")
    labels = [l.strip() for l in p["labels"].split(",") if l.strip()]
    if len(labels) < 2:
        raise PipelineError("give at least two labels")
    images = p["column"] == "image"
    if images and not {"asset_id", "path"} <= set(t.column_names):
        raise PipelineError("image labeling needs asset_id and path columns (use Load images)")
    if not images and p["column"] not in t.column_names:
        raise PipelineError(f"no column {p['column']!r}")
    rows = t.to_pylist()
    out_col = p["output"]
    todo = [i for i, r in enumerate(rows) if not (p["only_missing"] and r.get(out_col) not in (None, ""))][: p["max_rows"]]
    results: dict[int, tuple[str, float, str]] = {}

    settings = env.settings
    key = getattr(settings, "anthropic_api_key", None) if settings else None
    if key:
        import anthropic

        client = anthropic.Anthropic(api_key=key)

        def item(i: int) -> dict:
            r = rows[i]
            if images:
                root = env.asset_paths.get(r["asset_id"])
                if root is None:
                    raise PipelineError(f"asset {r['asset_id']} is not downloaded")
                return {"image": _image_block(root / r["path"])}
            return {"text": str(r.get(p["column"]) or "")[:4000]}

        batches = [todo[i : i + p["batch_size"]] for i in range(0, len(todo), p["batch_size"])]

        def run(batch):
            return _claude_batch(client, settings.planner_model, labels, p["instruction"], [(i, item(i)) for i in batch])

        with ThreadPoolExecutor(max_workers=4) as pool:
            for batch, fut in zip(batches, [pool.submit(run, b) for b in batches]):
                try:
                    for i, (label, conf) in fut.result().items():
                        if i in batch:
                            results[i] = (label, conf, "claude")
                except Exception as exc:
                    env.log(f"labeling batch failed: {exc}")
    else:
        env.log("no ANTHROPIC_API_KEY: keyword labeling; most rows will need review")
        for i in todo:
            label, conf = _heuristic(None if images else rows[i].get(p["column"]), labels)
            results[i] = (label, conf, "heuristic")

    review = 0
    for i, r in enumerate(rows):
        if i in results:
            label, conf, source = results[i]
            r[out_col] = None if label == "unknown" else label
            r["label_confidence"] = conf
            r["label_source"] = source
            r["needs_review"] = label == "unknown" or conf < p["threshold"]
        else:
            r.setdefault("label_confidence", None)
            r.setdefault("label_source", "existing" if r.get(out_col) not in (None, "") else None)
            r["needs_review"] = r.get(out_col) in (None, "") and i in todo
        review += bool(r["needs_review"])
    env.log(f"auto-label: {len(results)} labeled, {review} need review")
    fields = [f for f in t.schema if f.name not in (out_col, "label_confidence", "label_source", "needs_review")]
    schema = pa.schema(fields + [pa.field(out_col, pa.string()), pa.field("label_confidence", pa.float32()),
                                 pa.field("label_source", pa.string()), pa.field("needs_review", pa.bool_())])
    return Context(table=pa.Table.from_pylist(rows, schema=schema), videos=ctx.videos, files=ctx.files)
