"""Score search results on relevance and quality.

Relevance is lexical overlap between the data need + query and the result text.
It is a stand-in until embedding similarity + an LLM judge land (Phase 4).
"""

import math
import re
from datetime import datetime, timezone

from .connectors.base import RawResult
from .licensing import COMMERCIAL, NON_COMMERCIAL, RESEARCH_ONLY

_STOP = {
    "a", "an", "and", "the", "of", "for", "to", "in", "on", "with", "i", "want", "train", "build",
    "make", "model", "dataset", "data", "datasets", "ai", "my", "is", "that", "from", "using", "use",
    "by", "or", "be", "can", "this", "it", "as", "at", "existing", "pretrained",
}

_LICENSE_WEIGHT = {COMMERCIAL: 1.0, NON_COMMERCIAL: 0.7, RESEARCH_ONLY: 0.6}


def tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP and len(w) > 1}


def relevance(result: RawResult, need: str, query: str) -> float:
    want = tokenize(need) | tokenize(query)
    if not want:
        return 0.0
    title = tokenize(result.title.replace("/", " ").replace("-", " ").replace("_", " "))
    body = tokenize(result.description) | tokenize(" ".join(result.tags))
    query_terms = tokenize(query)
    title_hits = len(query_terms & title) / max(len(query_terms), 1)
    overall = len(want & (title | body)) / len(want)
    return round(min(1.0, 0.6 * title_hits + 0.4 * overall), 4)


def _log_scale(value: int | None, ceiling: float) -> float:
    if not value or value <= 0:
        return 0.0
    return min(1.0, math.log10(value + 1) / math.log10(ceiling + 1))


def quality(result: RawResult, license_class: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    popularity = max(_log_scale(result.downloads, 1_000_000), _log_scale(result.likes, 10_000))
    if result.updated_at:
        age_days = max((now - result.updated_at).days, 0)
        freshness = math.exp(-age_days / 730)  # ~2-year decay
    else:
        freshness = 0.3
    documented = 1.0 if result.description else 0.4
    usability = result.usability if result.usability is not None else 0.5
    license_score = _LICENSE_WEIGHT.get(license_class, 0.3)
    score = (
        0.40 * popularity
        + 0.20 * freshness
        + 0.15 * documented
        + 0.10 * usability
        + 0.15 * license_score
    )
    if result.meta.get("gated"):
        score *= 0.85
    return round(score, 4)


def combined(relevance_score: float, quality_score: float) -> float:
    return round(0.65 * relevance_score + 0.35 * quality_score, 4)
