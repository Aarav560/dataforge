"""Web pages -> text corpus, and PII scrubbing."""

import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import pyarrow as pa

from .engine import Context, Env, Param, PipelineError, step

USER_AGENT = "DataForgeBot/0.1 (+https://github.com/aarav560/dataforge)"
_SKIP = {"script", "style", "noscript", "nav", "footer", "header", "svg", "form", "aside"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre", "blockquote"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.links: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln)).strip()


def html_to_text(html: str) -> tuple[str, str, list[str]]:
    p = _Extractor()
    p.feed(html)
    return p.title.strip(), p.text(), p.links


def check_public_url(url: str) -> None:
    """Refuse non-HTTP URLs and hosts resolving to private, loopback or link-local addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PipelineError(f"not an http(s) URL: {url}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise PipelineError(f"cannot resolve {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise PipelineError(f"refusing to crawl non-public address {ip} ({parsed.hostname})")


class _Robots:
    def __init__(self, client: httpx.Client):
        self.client = client
        self.cache: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self.cache:
            rp = RobotFileParser()
            try:
                resp = self.client.get(base + "/robots.txt")
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
                self.cache[base] = rp
            except httpx.HTTPError:
                self.cache[base] = None
        rp = self.cache[base]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)


@step(
    "crawl_pages",
    "Crawl web pages",
    "source",
    "Fetch pages (respecting robots.txt and a per-host delay), optionally following same-site links, and extract text.",
    {
        "urls": Param("str", required=True, help="Start URLs, one per line or comma-separated"),
        "max_pages": Param("int", 50),
        "depth": Param("int", 0, "Link-following depth within the same site"),
        "delay": Param("float", 1.0, "Seconds between requests to one host"),
        "min_chars": Param("int", 200, "Drop pages with less text"),
    },
    source=True,
)
def crawl_pages(ctx: Context, p: dict, env: Env) -> Context:
    starts = [u.strip() for u in re.split(r"[\n,]", p["urls"]) if u.strip()]
    if not starts:
        raise PipelineError("give at least one URL")
    if not 1 <= p["max_pages"] <= 5000 or not 0 <= p["depth"] <= 3:
        raise PipelineError("max_pages must be 1-5000 and depth 0-3")
    queue = [(u, 0) for u in starts]
    seen: set[str] = set()
    last_hit: dict[str, float] = {}
    rows: dict[str, list] = {k: [] for k in ("url", "title", "text", "fetched_at")}
    with httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        robots = _Robots(client)
        while queue and len(rows["url"]) < p["max_pages"]:
            url, depth = queue.pop(0)
            url = url.split("#")[0]
            if url in seen:
                continue
            seen.add(url)
            try:
                check_public_url(url)
            except PipelineError as exc:
                env.log(str(exc))
                continue
            if not robots.allowed(url):
                env.log(f"robots.txt disallows {url}")
                continue
            host = urlparse(url).netloc
            wait = last_hit.get(host, 0) + p["delay"] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last_hit[host] = time.monotonic()
            try:
                # Redirects are followed by hand so each hop is checked as a public address.
                resp = client.get(url)
                hops = 0
                while resp.is_redirect and hops < 5:
                    nxt = urljoin(url, resp.headers.get("location", ""))
                    check_public_url(nxt)
                    url, hops = nxt, hops + 1
                    resp = client.get(url)
            except (httpx.HTTPError, PipelineError) as exc:
                env.log(f"failed {url}: {exc}")
                continue
            if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
                env.log(f"skipped {url}: HTTP {resp.status_code} {resp.headers.get('content-type', '')}")
                continue
            title, text, links = html_to_text(resp.text[:5_000_000])
            if len(text) >= p["min_chars"]:
                rows["url"].append(url)
                rows["title"].append(title)
                rows["text"].append(text)
                rows["fetched_at"].append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            if depth < p["depth"]:
                for href in links:
                    nxt = urljoin(url, href)
                    if urlparse(nxt).netloc == host and nxt.split("#")[0] not in seen:
                        queue.append((nxt, depth + 1))
    env.log(f"crawl: {len(rows['url'])} pages kept, {len(seen)} visited")
    return Context(table=pa.table(rows, schema=pa.schema([(k, pa.string()) for k in rows])))


_PII = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "ipv4": re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
}


def _luhn(digits: str) -> bool:
    nums = [int(d) for d in digits if d.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(nums)):
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def find_pii(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind, pattern in _PII.items():
        hits = pattern.findall(text)
        if kind == "credit_card":
            hits = [h for h in hits if _luhn(h)]
        if hits:
            counts[kind] = len(hits)
    return counts


def redact(text: str) -> str:
    text = _PII["credit_card"].sub(lambda m: "[CREDIT_CARD]" if _luhn(m.group()) else m.group(), text)
    for kind in ("email", "ssn", "phone", "ipv4"):
        text = _PII[kind].sub(f"[{kind.upper()}]", text)
    return text


@step(
    "pii_scrub",
    "PII scrub",
    "text",
    "Detect emails, phone numbers, SSNs, card numbers and IPs in text columns; redact, drop or flag them.",
    {
        "columns": Param("str", "", "Text columns (default: all string columns)"),
        "mode": Param("choice", "redact", "What to do with matches", ["redact", "drop", "flag"]),
    },
)
def pii_scrub(ctx: Context, p: dict, env: Env) -> Context:
    t = ctx.table
    if t is None:
        raise PipelineError("PII scrub needs a table input")
    cols = [c.strip() for c in p["columns"].split(",") if c.strip()] or [
        f.name for f in t.schema if pa.types.is_string(f.type) or pa.types.is_large_string(f.type)
    ]
    missing = [c for c in cols if c not in t.column_names]
    if missing:
        raise PipelineError(f"PII scrub: unknown column(s) {missing}")
    totals: dict[str, int] = {}
    rows, out = t.to_pylist(), []
    for row in rows:
        found: dict[str, int] = {}
        for c in cols:
            if isinstance(row.get(c), str):
                for k, n in find_pii(row[c]).items():
                    found[k] = found.get(k, 0) + n
        for k, n in found.items():
            totals[k] = totals.get(k, 0) + n
        if found and p["mode"] == "drop":
            continue
        if p["mode"] == "redact":
            row = {**row, **{c: redact(row[c]) for c in cols if isinstance(row.get(c), str)}}
        if p["mode"] == "flag":
            row = {**row, "pii": ",".join(sorted(found))}
        out.append(row)
    schema = t.schema.append(pa.field("pii", pa.string())) if p["mode"] == "flag" else t.schema
    env.log(f"pii: {totals or 'none found'} ({p['mode']})")
    return Context(table=pa.Table.from_pylist(out, schema=schema), videos=ctx.videos, files=ctx.files)
