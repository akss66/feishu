"""Collector composition that invokes Firecrawl once per source run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from commerce_agent.ingestion.collectors.base import Collector
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    CollectorKind,
    FetchContext,
    ResponseArtifact,
    SourceDefinition,
)
from commerce_agent.integrations.firecrawl import FirecrawlDocument, FirecrawlError


class FirecrawlScraper(Protocol):
    async def scrape(self, url: str) -> FirecrawlDocument: ...


class FirecrawlFirstCollector:
    """Call Firecrawl first and retain the native collector as authority."""

    def __init__(self, firecrawl: FirecrawlScraper, fallback: Collector) -> None:
        self._firecrawl = firecrawl
        self._fallback = fallback

    async def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem | CollectedFailure]:
        firecrawl_document: FirecrawlDocument | None = None
        try:
            firecrawl_document = await self._firecrawl.scrape(source.entry_url)
        except asyncio.CancelledError:
            raise
        except FirecrawlError as error:
            yield CollectedFailure(error.code)
        except Exception:
            yield CollectedFailure("firecrawl_failed")

        native_item_found = False
        native_failures: list[CollectedFailure] = []
        native_error: Exception | None = None
        try:
            async for item in self._fallback.collect(source, context):
                if isinstance(item, CollectedItem):
                    native_item_found = True
                    yield item
                else:
                    native_failures.append(item)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            native_error = error

        if native_item_found:
            for failure in native_failures:
                yield failure
        elif firecrawl_document is not None:
            yield _collected_item(source, firecrawl_document)
        else:
            for failure in native_failures:
                yield failure
        if native_error is not None and (native_item_found or firecrawl_document is None):
            raise native_error


def wrap_collectors_with_firecrawl(
    collectors: Mapping[CollectorKind, Collector],
    firecrawl: FirecrawlScraper | None,
) -> dict[CollectorKind, Collector]:
    if firecrawl is None:
        return dict(collectors)
    return {
        kind: FirecrawlFirstCollector(firecrawl, collector)
        for kind, collector in collectors.items()
    }


def _collected_item(
    source: SourceDefinition,
    document: FirecrawlDocument,
) -> CollectedItem:
    body = document.markdown.encode("utf-8")
    status_code = document.status_code or 200
    artifact = ResponseArtifact(
        url=document.url,
        status_code=status_code,
        headers={"content-type": "text/markdown; charset=utf-8"},
        body=body,
    )
    return CollectedItem(
        url=source.entry_url,
        body=body,
        content_type="text/markdown; charset=utf-8",
        title=document.title,
        artifact=artifact,
    )
