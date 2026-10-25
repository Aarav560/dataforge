"""Ready-made pipeline graphs."""

from .engine import linear

TEMPLATES = {
    "video_state_action": {
        "label": "Video → state/action pairs",
        "description": "Gameplay footage → frames → playfield → board matrix → inferred actions.",
        "needs_assets": True,
        "graph": lambda asset_ids: linear(
            ("assets", {"asset_ids": asset_ids}),
            ("sample_frames", {"fps": 0}),
            ("scene_filter", {}),
            ("detect_board", {}),
            ("grid_sample", {}),
            ("infer_actions", {}),
        ),
    },
    "tetris_synthetic": {
        "label": "Synthetic Tetris games",
        "description": "Simulate games with a heuristic agent and log (state, action, reward).",
        "needs_assets": False,
        "graph": lambda asset_ids: linear(("tetris_synthetic", {"games": 20, "agent": "mixed"})),
    },
    "arithmetic_synthetic": {
        "label": "Synthetic arithmetic",
        "description": "Generate math problems with exact answers.",
        "needs_assets": False,
        "graph": lambda asset_ids: linear(("arithmetic_synthetic", {})),
    },
    "web_text_corpus": {
        "label": "Web pages → text corpus",
        "description": "Crawl pages (robots.txt-aware), extract text, scrub PII, dedupe and chunk.",
        "needs_assets": False,
        "graph": lambda asset_ids: linear(
            ("crawl_pages", {"urls": "https://en.wikipedia.org/wiki/Tetris", "max_pages": 5}),
            ("pii_scrub", {"columns": "text"}),
            ("dedupe", {"columns": "text"}),
            ("chunk_text", {}),
        ),
    },
    "images_labeled": {
        "label": "Images → labeled classification set",
        "description": "Index images from downloads, label by folder, drop unlabeled, stratified split.",
        "needs_assets": True,
        "graph": lambda asset_ids: linear(
            ("load_images", {"asset_ids": asset_ids}),
            ("clean", {"required": "label"}),
            ("split", {"stratify": "label"}),
        ),
    },
    "csv_unified": {
        "label": "Multiple CSVs → unified table",
        "description": "Stack table files from downloads into one table, clean, dedupe and split.",
        "needs_assets": True,
        "graph": lambda asset_ids: linear(
            ("assets", {"asset_ids": asset_ids}),
            ("load_tables", {}),
            ("clean", {}),
            ("dedupe", {}),
            ("split", {}),
        ),
    },
}


def describe() -> list[dict]:
    return [
        {"id": k, "label": v["label"], "description": v["description"], "needs_assets": v["needs_assets"]}
        for k, v in TEMPLATES.items()
    ]


def build(template: str, asset_ids: list[int]) -> dict:
    if template not in TEMPLATES:
        raise KeyError(template)
    return TEMPLATES[template]["graph"](asset_ids)
