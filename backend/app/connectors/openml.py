from pathlib import Path

from .base import Connector, RawResult, stream_to_file

API = "https://www.openml.org/api/v1/json"


def _quality(item: dict, name: str):
    for q in item.get("quality") or []:
        if q.get("name") == name:
            try:
                return float(q["value"])
            except (TypeError, ValueError):
                return None
    return None


def parse_listing(item: dict) -> RawResult:
    did = item["did"]
    rows = _quality(item, "NumberOfInstances")
    feats = _quality(item, "NumberOfFeatures")
    classes = _quality(item, "NumberOfClasses")
    bits = [f"{int(rows):,} rows" if rows else None, f"{int(feats)} features" if feats else None,
            f"{int(classes)} classes" if classes else None]
    return RawResult(
        source="openml",
        kind="dataset",
        external_id=str(did),
        url=f"https://www.openml.org/d/{did}",
        title=f"{item['name']} (v{item.get('version', 1)})",
        description=", ".join(b for b in bits if b) or None,
        format=item.get("format"),
        # OpenML datasets are published under CC-BY unless stated otherwise.
        license="CC BY 4.0",
        meta={"rows": rows, "features": feats, "classes": classes},
    )


class OpenMLConnector(Connector):
    """OpenML's API only lists datasets by exact name; fuzzy search comes from the hub index."""

    name = "openml"
    kinds = frozenset({"dataset"})

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        name = query.strip().lower().replace(" ", "-")
        resp = await self.client.get(f"{API}/data/list/data_name/{name}/limit/{limit}/status/active")
        if resp.status_code == 412:  # OpenML's "no results"
            return []
        resp.raise_for_status()
        return [parse_listing(d) for d in resp.json().get("data", {}).get("dataset", [])]

    async def list_page(self, offset: int, limit: int) -> list[RawResult]:
        resp = await self.client.get(f"{API}/data/list/limit/{limit}/offset/{offset}/status/active")
        if resp.status_code == 412:
            return []
        resp.raise_for_status()
        return [parse_listing(d) for d in resp.json().get("data", {}).get("dataset", [])]

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        resp = await self.client.get(f"{API}/data/{external_id}")
        resp.raise_for_status()
        desc = resp.json()["data_set_description"]
        url = desc.get("parquet_url") or desc.get("url")
        name = f"{desc.get('name', external_id)}{'.parquet' if desc.get('parquet_url') else '.arff'}"
        return await stream_to_file(self.client, url, dest / name.replace("/", "_"), max_bytes)
