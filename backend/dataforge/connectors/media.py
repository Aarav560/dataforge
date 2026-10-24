"""Video, images & general web — YouTube (yt-dlp, no API key), Internet Archive, Openverse,
Wikimedia Commons, DuckDuckGo web search (free stand-in for Brave/Exa/Tavily)."""
from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from .. import http
from .base import Connector, Hit, clean_text


class YouTube(Connector):
    """Search via yt-dlp's `ytsearch` — no YouTube Data API key needed."""
    name, label, kinds = "youtube", "YouTube", ("video",)
    max_queries = 2

    async def search(self, query, limit):
        def run() -> list[dict]:
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            return info.get("entries", []) or []

        entries = await asyncio.wait_for(asyncio.to_thread(run), timeout=40)
        hits = []
        for e in entries:
            vid = e.get("id")
            if not vid:
                continue
            thumbs = e.get("thumbnails") or []
            hits.append(Hit(
                source=self.name, kind="video", url=e.get("url") or f"https://www.youtube.com/watch?v={vid}",
                title=e.get("title") or vid, description=e.get("description") or e.get("channel") or "",
                format="mp4", license="Standard YouTube License",
                meta={"duration": e.get("duration"), "views": e.get("view_count") or 0, "channel": e.get("channel"),
                      "thumbnail": thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                      "video_id": vid, "local_only": True},
                download={"type": "youtube", "url": f"https://www.youtube.com/watch?v={vid}"},
            ))
        return hits


class InternetArchive(Connector):
    name, label, kinds = "internet_archive", "Internet Archive", ("video", "dataset")

    async def search(self, query, limit):
        params = [("q", f"({query}) AND mediatype:(movies OR data OR software OR texts)"), ("rows", limit), ("output", "json"),
                  ("sort[]", "downloads desc")] + [("fl[]", f) for f in
                  ("identifier", "title", "description", "mediatype", "downloads", "item_size", "licenseurl", "date")]
        data = await http.get_json(f"https://archive.org/advancedsearch.php?{urlencode(params)}")
        hits = []
        for d in data.get("response", {}).get("docs", []):
            ident = d["identifier"]
            desc = d.get("description")
            if isinstance(desc, list):
                desc = " ".join(desc)
            title = d.get("title")
            if isinstance(title, list):
                title = title[0]
            hits.append(Hit(
                source=self.name, kind="video" if d.get("mediatype") == "movies" else "dataset",
                url=f"https://archive.org/details/{ident}", title=title or ident, description=clean_text(desc),
                size_bytes=d.get("item_size"), format=d.get("mediatype"), license=d.get("licenseurl"),
                meta={"downloads": d.get("downloads", 0), "updated": d.get("date"),
                      "thumbnail": f"https://archive.org/services/img/{ident}", "identifier": ident},
                download={"type": "archive_org", "identifier": ident},
            ))
        return hits


class Openverse(Connector):
    """CC-licensed images incl. Flickr CC — free anonymous API."""
    name, label, kinds = "openverse", "Openverse (incl. Flickr CC)", ("image",)

    async def search(self, query, limit):
        data = await http.get_json(f"https://api.openverse.org/v1/images/?{urlencode({'q': query, 'page_size': min(limit, 20)})}")
        return [Hit(
            source=self.name, kind="image", url=d.get("foreign_landing_url") or d["url"], title=d.get("title") or "untitled",
            description=f"by {d.get('creator') or 'unknown'} via {d.get('provider')}", size_bytes=d.get("filesize"),
            format=d.get("filetype") or "jpg",
            license=f"CC {d.get('license', '').upper()} {d.get('license_version', '')}".strip() if d.get("license") not in ("cc0", "pdm") else ("CC0" if d.get("license") == "cc0" else "Public Domain"),
            meta={"thumbnail": d.get("thumbnail") or d.get("url"), "creator": d.get("creator"), "provider": d.get("provider"),
                  "width": d.get("width"), "height": d.get("height"), "tags": [t["name"] for t in d.get("tags") or []][:10],
                  "attribution": d.get("attribution")},
            download={"type": "url", "url": d["url"]},
        ) for d in data.get("results", [])]


class WikimediaCommons(Connector):
    name, label, kinds = "wikimedia", "Wikimedia Commons", ("image", "video")

    async def search(self, query, limit):
        params = {"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query, "gsrlimit": limit,
                  "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 320, "format": "json"}
        data = await http.get_json(f"https://commons.wikimedia.org/w/api.php?{urlencode(params)}")
        hits = []
        for p in (data.get("query", {}).get("pages", {}) or {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            em = ii.get("extmetadata", {})
            mime = ii.get("mime", "")
            hits.append(Hit(
                source=self.name, kind="video" if mime.startswith("video") else "image",
                url=ii.get("descriptionurl", ""), title=p.get("title", "").removeprefix("File:"),
                description=clean_text(em.get("ImageDescription", {}).get("value")), size_bytes=ii.get("size"),
                format=mime.split("/")[-1] if mime else None, license=em.get("LicenseShortName", {}).get("value"),
                meta={"thumbnail": ii.get("thumburl"), "width": ii.get("width"), "height": ii.get("height"),
                      "artist": clean_text(em.get("Artist", {}).get("value"))},
                download={"type": "url", "url": ii.get("url")},
            ))
        return hits


class WebSearch(Connector):
    """General web via DuckDuckGo (ddgs) — free, no key. Pages are fetched later with robots.txt respected."""
    name, label, kinds = "web", "Web (DuckDuckGo)", ("web",)

    async def search(self, query, limit):
        def run():
            from ddgs import DDGS
            return DDGS().text(query, max_results=limit) or []

        rows = await asyncio.wait_for(asyncio.to_thread(run), timeout=25)
        return [Hit(source=self.name, kind="web", url=r.get("href", ""), title=r.get("title", ""), description=r.get("body", ""),
                    format="html", meta={}, download={"type": "webpage", "url": r.get("href")}) for r in rows if r.get("href")]
