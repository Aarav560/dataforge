"""Turn a plain-language goal into data needs and per-source search queries.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise (or on any API failure)
falls back to a keyword heuristic so the app still works offline.
"""

import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

from .config import Settings

log = logging.getLogger(__name__)

KINDS = ("dataset", "model", "environment", "code", "video", "paper")
APPROACHES = ("supervised", "reinforcement_learning", "imitation_learning", "self_supervised", "unsupervised")

# Which connectors serve each need kind.
KIND_SOURCES: dict[str, tuple[str, ...]] = {
    "dataset": ("huggingface", "kaggle", "github"),
    "model": ("huggingface", "github"),
    "environment": ("github",),
    "code": ("github",),
    "video": ("youtube",),
    "paper": ("arxiv", "semanticscholar"),
}


@dataclass
class PlannedNeed:
    description: str
    kind: str
    queries: list[str]

    def source_queries(self) -> dict[str, list[str]]:
        return {src: list(self.queries) for src in KIND_SOURCES[self.kind]}


@dataclass
class Plan:
    approach: str
    approach_reason: str
    needs: list[PlannedNeed] = field(default_factory=list)
    planner: str = "heuristic"

    def to_json(self) -> dict:
        return {
            "approach": self.approach,
            "approach_reason": self.approach_reason,
            "planner": self.planner,
            "needs": [
                {"description": n.description, "kind": n.kind, "queries": n.queries} for n in self.needs
            ],
        }


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "approach": {"type": "string", "enum": list(APPROACHES)},
        "approach_reason": {"type": "string"},
        "needs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["description", "kind", "queries"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["approach", "approach_reason", "needs"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You plan data collection for machine-learning projects.

Given a user's goal, decide the most suitable ML approach and list the distinct data needs required to train it. Each need is one of:
- dataset: existing labeled or raw datasets
- model: pretrained models or checkpoints usable as baselines, teachers or feature extractors
- environment: simulators or environments for generating data or RL training
- code: scrapers, loaders or preprocessing scripts that produce the data
- video: footage (e.g. gameplay, demonstrations) that can be turned into frames and labels
- paper: research papers whose methods or datasets are relevant

For each need give 1-3 short keyword search queries (2-4 words each) suited to search boxes on Hugging Face, Kaggle, GitHub, YouTube and arXiv. Queries are matched lexically, so use the words dataset authors would put in titles, not full sentences. Return 3-6 needs, most important first."""


async def plan_with_claude(goal: str, settings: Settings) -> Plan:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.beta.messages.create(
        model=settings.planner_model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Goal: {goal}"}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("planner request was declined")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("planner output was truncated")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    needs = []
    for raw in data["needs"]:
        queries = [q.strip() for q in raw["queries"] if q.strip()][:3]
        if raw["kind"] in KINDS and queries:
            needs.append(PlannedNeed(raw["description"].strip(), raw["kind"], queries))
    if not needs:
        raise RuntimeError("planner returned no usable needs")
    return Plan(data["approach"], data["approach_reason"], needs, planner="claude")


_FILLER = re.compile(
    r"\b(i|we|want|wants|would|like|need|to|train|build|make|create|an?|the|ai|model|agent|bot|"
    r"that|which|can|for|my|our|using|with|ml|machine|learning|system|neural|network|please|"
    r"from|of|on|in|into|and|or|about|by|is|are|it|its|"
    r"classify|classifier|classification|detect|detector|detection|predict|predictor|prediction|"
    r"recognize|recognise|recognition|generate|generator|generation|segment|segmentation|identify|"
    r"forecast|forecasting|estimate|estimation|play|plays|playing|learn|learns|teach)\b",
    re.IGNORECASE,
)

_RL_HINTS = ("game", "play", "tetris", "chess", "atari", "robot", "control", "drive", "driving", "agent", "trading")
_SSL_HINTS = ("language model", "llm", "chatbot", "text generation", "embedding")


def _topic(goal: str) -> str:
    topic = _FILLER.sub(" ", goal)
    topic = re.sub(r"[^\w\s-]", " ", topic)
    words = topic.split()
    return " ".join(words[:3]) if words else goal.strip()[:40]


def plan_heuristic(goal: str) -> Plan:
    topic = _topic(goal)
    lowered = goal.lower()
    if any(h in lowered for h in _RL_HINTS):
        approach = "reinforcement_learning"
        reason = "The goal involves sequential decisions, so RL (optionally bootstrapped by imitation) fits."
    elif any(h in lowered for h in _SSL_HINTS):
        approach = "self_supervised"
        reason = "Generative text goals are usually pretrained self-supervised, then fine-tuned."
    else:
        approach = "supervised"
        reason = "Default to supervised learning on labeled examples."

    needs = [
        PlannedNeed(f"Existing {topic} datasets", "dataset", [topic, f"{topic} dataset"]),
        PlannedNeed(f"Pretrained {topic} models to use as baselines", "model", [topic]),
    ]
    if approach == "reinforcement_learning":
        needs.append(
            PlannedNeed(f"{topic} simulator or environment", "environment", [f"{topic} gym", f"{topic} environment"])
        )
        needs.append(PlannedNeed(f"{topic} gameplay or demonstration footage", "video", [f"{topic} gameplay"]))
    method = {"reinforcement_learning": "reinforcement learning", "self_supervised": "language model"}.get(approach, "")
    needs.append(PlannedNeed(f"Research papers on {topic}", "paper", [f"{topic} {method}".strip()]))
    needs.append(PlannedNeed(f"Scrapers and loaders for {topic} data", "code", [f"{topic} scraper", f"{topic} data loader"]))
    return Plan(approach, reason, needs, planner="heuristic")


async def make_plan(goal: str, settings: Settings) -> Plan:
    if settings.anthropic_api_key:
        try:
            return await plan_with_claude(goal, settings)
        except anthropic.AuthenticationError:
            log.error("ANTHROPIC_API_KEY was rejected; using the heuristic planner")
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            log.warning("Claude planner unavailable (%s); using the heuristic planner", exc)
        except (RuntimeError, ValueError, KeyError, StopIteration) as exc:
            log.warning("Claude planner returned an unusable plan (%s); using the heuristic planner", exc)
    return plan_heuristic(goal)
