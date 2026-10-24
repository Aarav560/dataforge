"""Goal planner (plan §3.1): goal → ML approach → data needs → per-source queries.

Uses the local Ollama model when available and always validates/merges with a deterministic
rule-based plan, so planning works fully offline and never depends on a paid API.
"""
from __future__ import annotations

import re

from . import llm
from .connectors import KIND_SOURCES, REGISTRY
from .db import new_id
from .synthetic import advisor

KINDS = list(KIND_SOURCES)

DOMAINS: dict[str, list[str]] = {
    "game": ["game", "tetris", "chess", "atari", "mario", "minecraft", "poker", "go ", "snake", "pong", "doom", "starcraft",
             "dota", "puzzle", "2048", "sudoku", "board", "play ", "player", "pacman", "flappy"],
    "robotics": ["robot", "arm", "grasp", "drone", "locomotion", "manipulat", "self-driving", "autonomous", "driving", "quadruped"],
    "vision": ["image", "photo", "detect", "segment", "classif", "vision", "camera", "face", "x-ray", "xray", "scan",
               "satellite", "ocr", "pose", "object", "picture", "medical imag", "plant", "bird", "car ", "face recogni", "image recogni"],
    "audio": ["audio", "speech", "voice", "music", "sound", "asr", "transcri", "speaker", "song"],
    "nlp": ["text", "language", "chat", "sentiment", "translat", "summar", "question", "nlp", "llm", "token", "document",
            "review", "tweet", "email", "spam", "code generation", "named entity"],
    "timeseries": ["stock", "forecast", "time series", "timeseries", "sensor", "weather", "price", "energy", "traffic", "sales"],
    "tabular": ["tabular", "churn", "fraud", "credit", "loan", "house", "insurance", "csv", "customer", "medical record", "diabetes"],
    "math": ["math", "arithmetic", "equation", "theorem", "algebra", "calculus", "symbolic"],
}

APPROACHES = {
    "game": ("reinforcement learning", ["imitation learning (behaviour cloning from gameplay)", "supervised (state → best move)"],
             "Games have a simulator, so an agent can generate unlimited experience; human play logs/video bootstrap via imitation."),
    "robotics": ("reinforcement learning", ["imitation learning from demonstrations", "sim-to-real transfer"],
                 "Physics simulators are free and safe; real demonstrations help close the sim-to-real gap."),
    "vision": ("supervised learning", ["fine-tune a pretrained backbone", "self-supervised pretraining + linear probe"],
               "Labeled images are the bottleneck — start from a pretrained model and a public labeled set."),
    "audio": ("supervised learning", ["fine-tune a pretrained speech/audio model"], "Fine-tuning pretrained audio encoders needs far less labeled audio."),
    "nlp": ("supervised fine-tuning", ["instruction tuning", "retrieval-augmented generation"], "Start from a pretrained language model and a task-specific corpus."),
    "timeseries": ("supervised forecasting", ["gradient-boosted trees on lag features", "temporal deep nets"], "Historical series with covariates; trees are a strong baseline."),
    "tabular": ("supervised learning", ["gradient-boosted trees", "AutoML"], "Tabular problems are dominated by GBDTs; data quality matters most."),
    "math": ("supervised fine-tuning", ["synthetic problem generation + verification"], "Math data can be generated and checked programmatically."),
    "general": ("supervised learning", ["fine-tune a pretrained model"], "Find an existing labeled dataset close to the task, then fine-tune."),
}

_FILLER = re.compile(r"^(i\s+)?(want|would like|need|wanna|am trying|'m trying|plan)?\s*(to\s+)?(train|build|make|create|develop|teach)?\s*(an?|the|my)?\s*", re.I)
_TAIL = re.compile(r"\s+(ai|a\.i\.|model|agent|bot|classifier|detector|system|network|net|app)\b.*$", re.I)


_LEAD_NOUN = re.compile(r"^(ai|a\.i\.|model|agent|bot|classifier|detector|system|network|app|program)\s+(that|which|to|for)\s+(can\s+)?", re.I)
_VERB = re.compile(r"^(detects?|detecting|classif(?:y|ies|ying)|predicts?|predicting|recogni[sz]es?|generates?|plays?|playing|"
                   r"translates?|segments?|forecasts?|identif(?:y|ies)|counts?|tracks?|reads?|understands?|summari[sz]es?|beats?|solves?)\s+", re.I)
_MEDIUM = re.compile(r"\s+(?:from|in|using|with)\s+(?:(\w+)\s+)?(?:photos?|images?|pictures?|videos?|text|camera|webcam|footage)$", re.I)


def subject_of(goal: str) -> str:
    s = _FILLER.sub("", goal.strip().rstrip(".!?"))
    s = _LEAD_NOUN.sub("", s)
    s2 = _TAIL.sub("", s)
    s = (s2 or s).strip()
    s = re.sub(r"^(that|which|to)\s+(can\s+)?", "", s, flags=re.I)
    s = _VERB.sub("", s)
    s = _MEDIUM.sub(lambda m: f" {m.group(1)}" if m.group(1) else "", s).strip()  # "from leaf photos" → "leaf"
    return s[:80] or goal[:80]


def detect_domain(goal: str) -> str:
    g = f" {goal.lower()} "
    scores = {d: sum(1 for k in kws if k in g) for d, kws in DOMAINS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def heuristic_plan(goal: str) -> dict:
    subj = subject_of(goal)
    domain = detect_domain(goal)
    primary, alts, why = APPROACHES[domain]
    needs: list[dict] = []

    def need(kind: str, description: str, queries: list[str], priority: int = 2):
        needs.append({"id": new_id(), "kind": kind, "description": description,
                      "queries": [q for q in dict.fromkeys(queries) if q], "sources": KIND_SOURCES[kind],
                      "status": "pending", "priority": priority})

    need("dataset", f"Existing labeled datasets for {subj}", [subj, f"{subj} dataset"], 1)
    if domain in ("game", "robotics"):
        need("environment", f"Simulator / environment for {subj}", [subj, f"{subj} gym"], 1)
        need("video", f"Gameplay / demonstration footage of {subj}", [f"{subj} gameplay", f"{subj} expert play"], 2)
        need("model", f"Pretrained agents / baseline policies for {subj}", [f"{subj} agent", f"{subj} reinforcement learning"], 2)
        need("paper", f"Research on learning {subj}", [f"{subj} reinforcement learning", f"{subj} artificial intelligence"], 3)
    elif domain == "vision":
        need("image", f"Openly-licensed images of {subj}", [subj], 2)
        need("model", f"Pretrained vision models for {subj}", [subj, f"{subj} classification"], 2)
        need("paper", f"Papers & benchmarks on {subj}", [f"{subj} dataset benchmark", f"{subj} deep learning"], 3)
    elif domain == "audio":
        need("model", f"Pretrained audio models for {subj}", [subj], 2)
        need("video", f"Audio/video recordings of {subj}", [subj], 3)
        need("paper", f"Papers on {subj}", [f"{subj} deep learning"], 3)
    elif domain == "nlp":
        need("model", f"Pretrained language models for {subj}", [subj], 2)
        need("web", f"Web text corpus about {subj}", [f"{subj} corpus", f"{subj} text dataset download"], 3)
        need("paper", f"Papers on {subj}", [f"{subj} natural language processing"], 3)
    else:
        need("model", f"Pretrained models / baselines for {subj}", [subj], 3)
        need("paper", f"Papers on {subj}", [f"{subj} machine learning"], 3)
        need("web", f"Open-data portals & web sources for {subj}", [f"{subj} open data csv download"], 3)
    need("code", f"Loaders, scrapers & preprocessing scripts for {subj}", [f"{subj} dataset", f"{subj} scraper"], 3)

    return {
        "goal": goal, "subject": subj, "domain": domain,
        "approach": {"primary": primary, "alternatives": alts, "rationale": why},
        "needs": needs, "synthetic": advisor.advise(goal, domain, subj), "planner": "rules",
    }


PLAN_SYSTEM = f"""You are the planning module of an ML dataset research tool.
Given a user's ML goal, decide the best ML approach and list the DATA NEEDS required to build it.
Each need has a kind from {KINDS}, a one-line description, and 1-3 short search queries (2-5 words, keyword style, no quotes).
Respond ONLY with JSON:
{{"subject": "<short noun phrase>", "approach": {{"primary": "...", "alternatives": ["..."], "rationale": "<1 sentence>"}},
 "needs": [{{"kind": "dataset", "description": "...", "queries": ["...", "..."]}}]}}"""


async def make_plan(goal: str, use_llm: bool = True) -> dict:
    base = heuristic_plan(goal)
    if not use_llm:
        return base
    out = await llm.complete_json(PLAN_SYSTEM, f"Goal: {goal}")
    if not isinstance(out, dict):
        return base
    plan = dict(base)
    plan["planner"] = f"llm:{await llm.model_name()}+rules"
    appr = out.get("approach")
    if isinstance(appr, dict) and isinstance(appr.get("primary"), str) and appr["primary"].strip():
        plan["approach"] = {"primary": appr["primary"][:80],
                            "alternatives": [str(a)[:100] for a in (appr.get("alternatives") or []) if a][:4] or base["approach"]["alternatives"],
                            "rationale": str(appr.get("rationale") or base["approach"]["rationale"])[:300]}
    # Merge LLM queries into matching heuristic needs, add new kinds it proposes.
    by_kind = {n["kind"]: n for n in plan["needs"]}
    for n in out.get("needs") or []:
        if not isinstance(n, dict) or n.get("kind") not in KINDS:
            continue
        qs = [re.sub(r"[\"']", "", str(q)).strip()[:60] for q in (n.get("queries") or []) if isinstance(q, str) and q.strip()][:3]
        if n["kind"] in by_kind:
            tgt = by_kind[n["kind"]]
            tgt["queries"] = list(dict.fromkeys(tgt["queries"] + qs))[:4]
        elif qs:
            new = {"id": new_id(), "kind": n["kind"], "description": str(n.get("description") or f"{n['kind']} for {base['subject']}")[:160],
                   "queries": qs, "sources": KIND_SOURCES[n["kind"]], "status": "pending", "priority": 3}
            plan["needs"].append(new)
            by_kind[n["kind"]] = new
    return plan


def validate_plan(plan: dict) -> dict:
    """Sanitize a user-edited plan tree."""
    needs = []
    for n in plan.get("needs", []):
        kind = n.get("kind")
        if kind not in KINDS:
            continue
        sources = [s for s in (n.get("sources") or KIND_SOURCES[kind]) if s in REGISTRY]
        needs.append({"id": n.get("id") or new_id(), "kind": kind, "description": str(n.get("description", ""))[:200],
                      "queries": [str(q)[:80] for q in (n.get("queries") or []) if str(q).strip()][:6] or [plan.get("subject", "")],
                      "sources": sources or KIND_SOURCES[kind], "status": n.get("status", "pending"),
                      "priority": int(n.get("priority", 2)), "enabled": n.get("enabled", True)})
    plan["needs"] = needs
    return plan
