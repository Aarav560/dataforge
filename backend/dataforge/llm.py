"""Local LLM via Ollama — free, runs on the user's machine. All callers must handle `None`."""
from __future__ import annotations

import json
import re

import httpx

from .config import LLM_TIMEOUT, OLLAMA_MODEL, OLLAMA_URL

_model_cache: str | None = None
_unavailable_until = 0.0


async def model_name() -> str | None:
    global _model_cache, _unavailable_until
    import time
    if _model_cache:
        return _model_cache
    if time.time() < _unavailable_until:
        return None
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        _unavailable_until = time.time() + 30
        return None
    if not names:
        _unavailable_until = time.time() + 30
        return None
    if OLLAMA_MODEL and OLLAMA_MODEL in names:
        _model_cache = OLLAMA_MODEL
    else:
        # Prefer instruction-tuned general models; skip embedding-only models.
        chat = [n for n in names if "embed" not in n]
        _model_cache = (chat or names)[0]
    return _model_cache


async def status() -> dict:
    m = await model_name()
    return {"provider": "ollama", "url": OLLAMA_URL, "model": m, "available": m is not None}


async def complete_json(system: str, prompt: str, temperature: float = 0.2) -> dict | list | None:
    """Ask the local model for JSON. Returns None if the LLM is unavailable or output is unparseable."""
    model = await model_name()
    if not model:
        return None
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": 4096},
    }
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as c:
            r = await c.post(f"{OLLAMA_URL}/api/chat", json=body)
            r.raise_for_status()
            text = r.json()["message"]["content"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    return parse_json_loose(text)


def parse_json_loose(text: str) -> dict | list | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None
