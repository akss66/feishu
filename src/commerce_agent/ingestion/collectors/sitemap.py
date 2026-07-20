from __future__ import annotations

from collections.abc import AsyncIterator
from xml.etree import ElementTree

from commerce_agent.ingestion.collectors.base import (
    CollectorError,
    HttpPort,
    candidate_url,
    fetch_request,
    item_limit,
    record_response,
    require_success,
    response_artifact,
)
from commerce_agent.ingestion.models import CollectedItem, FetchContext, SourceDefinition

_MAX_SITEMAPS = 256


class SitemapCollector:
    def __init__(self, http_port: HttpPort) -> None:
        self._http = http_port

    async def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem]:
        stack: list[tuple[str, bool]] = [(source.entry_url, True)]
        visited_sitemaps: set[str] = set()
        seen_items: set[str] = set()
        limit = item_limit(source)

        while stack and len(seen_items) < limit:
            sitemap_url, is_root = stack.pop()
            if sitemap_url in visited_sitemaps:
                continue
            if len(visited_sitemaps) >= _MAX_SITEMAPS:
                raise CollectorError("item_limit_exceeded")
            visited_sitemaps.add(sitemap_url)
            response = await self._http.get(
                fetch_request(source, context, url=sitemap_url, conditional=is_root)
            )
            record_response(context, response)
            if not require_success(response):
                continue
            try:
                root = ElementTree.fromstring(response.body)
            except ElementTree.ParseError:
                raise CollectorError("invalid_payload") from None

            root_name = _local_name(root.tag)
            if root_name == "sitemapindex":
                nested = _locations(root, "sitemap", response.url)
                stack.extend((url, False) for url in reversed(nested))
                continue
            if root_name != "urlset":
                raise CollectorError("invalid_payload")

            for url in _locations(root, "url", response.url):
                if url in seen_items:
                    continue
                seen_items.add(url)
                detail = await self._http.get(
                    fetch_request(source, context, url=url, conditional=False)
                )
                record_response(context, detail)
                if not require_success(detail):
                    continue
                artifact = response_artifact(detail)
                yield CollectedItem(
                    url=detail.url,
                    body=detail.body,
                    content_type=artifact.headers.get("content-type"),
                    etag=detail.etag,
                    last_modified=detail.last_modified,
                    artifact=artifact,
                )
                if len(seen_items) >= limit:
                    return


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _locations(root: ElementTree.Element, item_name: str, base_url: str) -> list[str]:
    locations: list[str] = []
    for item in root:
        if _local_name(item.tag) != item_name:
            continue
        raw = next(
            (
                "".join(child.itertext()).strip()
                for child in item
                if _local_name(child.tag) == "loc"
            ),
            None,
        )
        url = candidate_url(base_url, raw)
        if url is not None:
            locations.append(url)
    return locations
