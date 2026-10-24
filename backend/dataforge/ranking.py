"""Ranking & vetting (plan §3.3): relevance, quality, licence, duplicates.

Relevance = lexical TF-IDF cosine (instant, free) blended with an optional local-LLM judge.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone

from . import llm
from .licenses import classify

_STOP = set("""a an the and or of for to in on with from by is are be this that these those it its as at into using use
based via we our your their data dataset datasets model models train training learning machine ml ai i want""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower().replace("_", " ").replace("-", " ")) if t not in _STOP and len(t) > 1]


def _tf(toks: list[str]) -> Counter:
    return Counter(toks)


def cosine(a: Counter, b: Counter, idf: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[t] * b[t] * idf.get(t, 1.0) ** 2 for t in a if t in b)
    na = math.sqrt(sum((v * idf.get(t, 1.0)) ** 2 for t, v in a.items()))
    nb = math.sqrt(sum((v * idf.get(t, 1.0)) ** 2 for t, v in b.items()))
    return dot / (na * nb) if na and nb else 0.0


def relevance(results: list[dict], need_text: str, goal: str) -> None:
    """Score each result in place against its data need (+ the overall goal, lower weight)."""
    docs = [_tf(tokens(f"{r['title']} {r['title']} {r.get('description', '')} {' '.join(map(str, (r.get('meta') or {}).get('tags', []) or []))}")) for r in results]
    n = len(docs) + 1
    df = Counter(t for d in docs for t in d)
    idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
    need_v, goal_v = _tf(tokens(need_text)), _tf(tokens(goal))
    key_terms = set(tokens(goal)) | set(tokens(need_text))
    for r, d in zip(results, docs):
        title_toks = set(tokens(r["title"]))
        title_hit = len(key_terms & title_toks) / max(1, min(len(key_terms), 3))
        s = 0.55 * cosine(need_v, d, idf) + 0.25 * cosine(goal_v, d, idf) + 0.2 * min(1.0, title_hit)
        r["relevance"] = round(min(1.0, s * 1.6), 4)


def _recency(ts: str | None) -> float:
    if not ts:
        return 0.4
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        m = re.match(r"(\d{4})", str(ts))
        if not m:
            return 0.4
        dt = datetime(int(m.group(1)), 6, 1, tzinfo=timezone.utc)
    years = (datetime.now(timezone.utc) - dt).days / 365
    return max(0.0, 1 - years / 8)


def quality(r: dict) -> float:
    m = r.get("meta") or {}
    pop = max(m.get("downloads") or 0, (m.get("stars") or 0) * 20, (m.get("citations") or 0) * 50,
              (m.get("likes") or 0) * 40, (m.get("votes") or 0) * 30, (m.get("views") or 0) / 50)
    popularity = min(1.0, math.log10(pop + 1) / 6)
    year = m.get("year")
    recency = _recency(m.get("updated") or m.get("published") or (f"{year}-06-01" if year else None))
    completeness = sum(bool(x) for x in (r.get("description"), r.get("license"), r.get("format"),
                                          r.get("size_bytes") or m.get("rows_estimate"))) / 4
    penalty = 0.0
    if m.get("archived"):
        penalty += 0.15
    if m.get("gated"):
        penalty += 0.1
    if m.get("rows_estimate") and m.get("missing_values"):
        penalty += min(0.2, m["missing_values"] / max(1, m["rows_estimate"]) * 0.1)
    if r.get("kind") in ("environment",) and (r.get("meta") or {}).get("pip"):
        popularity = max(popularity, 0.7)  # curated simulators
    return round(max(0.0, 0.5 * popularity + 0.25 * recency + 0.25 * completeness - penalty), 4)


def vet(r: dict) -> dict:
    cls, rights = classify(r.get("license"))
    if (r.get("meta") or {}).get("local_only"):
        cls = "local_only"
        rights = {"commercial": False, "redistribution": False, "attribution": True, "share_alike": None}
    r["license_class"] = cls
    r.setdefault("meta", {})["rights"] = rights
    r["quality"] = quality(r)
    lic_factor = {"commercial_ok": 1.0, "copyleft": 0.95, "non_commercial": 0.85, "research_only": 0.85,
                  "no_derivatives": 0.8, "unknown": 0.8, "local_only": 0.8}[cls]
    # Relevance gates the score: a popular-but-irrelevant result should never outrank a relevant one.
    r["score"] = round(r.get("relevance", 0) * (0.55 + 0.45 * r["quality"]) * lic_factor, 4)
    return r


def _norm_title(t: str) -> str:
    t = t.lower().split("/")[-1]
    return re.sub(r"[^a-z0-9]", "", t)


def mark_duplicates(results: list[dict]) -> None:
    """Flag the same dataset mirrored across hubs (same normalized name or same URL)."""
    seen: dict[str, dict] = {}
    for r in sorted(results, key=lambda x: -x.get("score", 0)):
        keys = {r["url"].rstrip("/").lower()}
        nt = _norm_title(r["title"])
        if len(nt) >= 6:
            keys.add(f"{r['kind']}:{nt}")
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit and hit["id"] != r["id"]:
            r["dup_of"] = hit["id"]
        else:
            for k in keys:
                seen[k] = r


JUDGE_SYSTEM = (
    "You vet search results for an ML dataset research tool. For each result, rate how useful it is for the stated "
    "data need on a 0-10 scale and give a short reason. Respond as JSON: "
    '{"ratings":[{"i":<index>,"score":<0-10>,"reason":"<=15 words"}]}'
)


async def llm_judge(results: list[dict], need: str, goal: str, top: int = 8) -> bool:
    """Optional local-LLM relevance judge on the top results. Mutates in place. Returns False if unavailable."""
    subset = sorted(results, key=lambda r: -r.get("score", 0))[:top]
    if not subset:
        return False
    listing = "\n".join(f"[{i}] ({r['source']}/{r['kind']}) {r['title']} — {(r.get('description') or '')[:160]}" for i, r in enumerate(subset))
    out = await llm.complete_json(JUDGE_SYSTEM, f"Goal: {goal}\nData need: {need}\n\nResults:\n{listing}")
    if not isinstance(out, dict) or not isinstance(out.get("ratings"), list):
        return False
    for item in out["ratings"]:
        try:
            r = subset[int(item["i"])]
            judged = max(0.0, min(10.0, float(item["score"]))) / 10
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        r["relevance"] = round(0.5 * r.get("relevance", 0) + 0.5 * judged, 4)
        r.setdefault("meta", {})["llm_reason"] = str(item.get("reason", ""))[:160]
        vet(r)
    return True
