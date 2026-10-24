"""Connector interface. Every connector uses free, public endpoints."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class Hit:
    source: str
    kind: str  # dataset | model | environment | paper | video | image | web | code
    url: str
    title: str
    description: str = ""
    size_bytes: int | None = None
    format: str | None = None
    license: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # How the gather step should fetch it, e.g. {"type": "hf_dataset", "repo": "x/y"}
    download: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source, "kind": self.kind, "url": self.url, "title": self.title,
            "description": clean_text(self.description)[:600], "size_bytes": self.size_bytes,
            "format": self.format, "license": self.license, "meta": {**self.meta, "download": self.download},
        }


class Connector:
    name: ClassVar[str]
    label: ClassVar[str]
    kinds: ClassVar[tuple[str, ...]]
    requires: ClassVar[str | None] = None  # name of optional free credential, informational
    max_queries: ClassVar[int] = 3  # per data need — keeps free-tier rate limits happy

    def available(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> list[Hit]:  # pragma: no cover - interface
        raise NotImplementedError


_TAG = re.compile(r"<[^>]+>")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s))).strip()


def parse_size(s: str | None) -> int | None:
    """'1.2 GB' → bytes."""
    if not s:
        return None
    m = re.match(r"([\d.]+)\s*([KMGT]?)i?B", str(s).strip(), re.I)
    if not m:
        return None
    mult = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)
