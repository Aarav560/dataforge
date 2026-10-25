import asyncio
from pathlib import Path

from .base import Connector, DownloadTooLarge, RawResult, parse_dt

API = "https://www.googleapis.com/youtube/v3"
# YouTube's default license allows viewing, not redistribution.
STANDARD_LICENSE = "YouTube Standard License"
CC_LICENSE = "CC BY 3.0 (YouTube Creative Commons)"


def _ydl(opts: dict):
    from yt_dlp import YoutubeDL

    return YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, **opts})


class YouTubeConnector(Connector):
    """Search via the YouTube Data API when a key is set, otherwise yt-dlp."""

    name = "youtube"
    kinds = frozenset({"video"})

    def __init__(self, client, api_key: str | None = None, max_height: int = 720):
        super().__init__(client)
        self.api_key = api_key
        self.max_height = max_height

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        if self.api_key:
            return await self._search_api(query, limit)
        return await asyncio.to_thread(self._search_ytdlp, query, limit)

    async def _search_api(self, query: str, limit: int) -> list[RawResult]:
        resp = await self.client.get(
            f"{API}/search",
            params={"part": "snippet", "q": query, "type": "video", "maxResults": min(limit, 50), "key": self.api_key},
        )
        resp.raise_for_status()
        ids = [it["id"]["videoId"] for it in resp.json().get("items", []) if it.get("id", {}).get("videoId")]
        if not ids:
            return []
        details = await self.client.get(
            f"{API}/videos",
            params={"part": "snippet,statistics,status,contentDetails", "id": ",".join(ids), "key": self.api_key},
        )
        details.raise_for_status()
        out = []
        for v in details.json().get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            cc = v.get("status", {}).get("license") == "creativeCommon"
            out.append(
                RawResult(
                    source=self.name,
                    kind="video",
                    external_id=v["id"],
                    url=f"https://www.youtube.com/watch?v={v['id']}",
                    title=sn.get("title", v["id"]),
                    description=sn.get("description"),
                    format="video",
                    license=CC_LICENSE if cc else STANDARD_LICENSE,
                    updated_at=parse_dt(sn.get("publishedAt")),
                    downloads=int(st["viewCount"]) if st.get("viewCount") else None,
                    likes=int(st["likeCount"]) if st.get("likeCount") else None,
                    meta={
                        "channel": sn.get("channelTitle"),
                        "duration": v.get("contentDetails", {}).get("duration"),
                        "thumbnail": (sn.get("thumbnails", {}).get("medium") or {}).get("url"),
                        "redistributable": cc,
                    },
                )
            )
        return out

    def _search_ytdlp(self, query: str, limit: int) -> list[RawResult]:
        with _ydl({"extract_flat": "in_playlist", "skip_download": True}) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        out = []
        for e in (info or {}).get("entries") or []:
            if not e or not e.get("id"):
                continue
            thumbs = e.get("thumbnails") or []
            out.append(
                RawResult(
                    source=self.name,
                    kind="video",
                    external_id=e["id"],
                    url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                    title=e.get("title") or e["id"],
                    description=e.get("description"),
                    format="video",
                    # Flat search doesn't report licenses; assume the restrictive default.
                    license=STANDARD_LICENSE,
                    downloads=e.get("view_count"),
                    meta={
                        "channel": e.get("channel"),
                        "duration": e.get("duration"),
                        "thumbnail": thumbs[-1]["url"] if thumbs else None,
                        "redistributable": False,
                    },
                )
            )
        return out

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        return await asyncio.to_thread(self._download, external_id, dest, max_bytes)

    def _download(self, external_id: str, dest: Path, max_bytes: int) -> int:
        h = self.max_height
        opts = {
            "format": f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/b[height<={h}]/b",
            "merge_output_format": "mp4",
            "outtmpl": str(dest / "%(id)s.%(ext)s"),
            "max_filesize": max_bytes,
            "writeinfojson": True,
        }
        with _ydl(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={external_id}"])
        total = sum(p.stat().st_size for p in dest.iterdir() if p.is_file())
        if not any(p.suffix in (".mp4", ".mkv", ".webm") for p in dest.iterdir()):
            raise DownloadTooLarge(f"video {external_id} was skipped (larger than {max_bytes} bytes or unavailable)")
        return total
