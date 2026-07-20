from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

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
from commerce_agent.ingestion.collectors.feed import _parse_datetime
from commerce_agent.ingestion.models import CollectedItem, FetchContext, SourceDefinition


class ApiCollector:
    def __init__(self, http_port: HttpPort) -> None:
        self._http = http_port

    async def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem]:
        response = await self._http.get(fetch_request(source, context))
        record_response(context, response)
        if not require_success(response):
            return
        artifact = response_artifact(response)
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise CollectorError("invalid_payload") from None

        items_path = _config_string(source, "items_path")
        url_field = _config_string(source, "url_field")
        title_field = _optional_config_string(source, "title_field")
        published_field = _optional_config_string(source, "published_at_field")
        raw_items = _path_value(payload, items_path)
        if not isinstance(raw_items, list):
            raise CollectorError("invalid_payload")

        seen: set[str] = set()
        limit = item_limit(source)
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            raw_url = _path_value(raw_item, url_field)
            url = candidate_url(response.url, raw_url if isinstance(raw_url, str) else None)
            if url is None or url in seen:
                continue
            seen.add(url)
            raw_title = _path_value(raw_item, title_field) if title_field else None
            raw_published = _path_value(raw_item, published_field) if published_field else None
            title = (
                raw_title.strip()
                if isinstance(raw_title, str) and raw_title.strip()
                else None
            )
            published_at = _parse_datetime(
                raw_published if isinstance(raw_published, str) else None
            )
            yield CollectedItem(
                url=url,
                body=json.dumps(
                    raw_item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
                content_type="application/json",
                title=title,
                published_at=published_at,
                etag=response.etag,
                last_modified=response.last_modified,
                artifact=artifact,
            )
            if len(seen) >= limit:
                return


def _config_string(source: SourceDefinition, name: str) -> str:
    value = source.collector_config.get(name)
    if not isinstance(value, str) or not value:
        raise CollectorError("invalid_config")
    return value


def _optional_config_string(source: SourceDefinition, name: str) -> str | None:
    value = source.collector_config.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise CollectorError("invalid_config")
    return value


def _path_value(value: Any, path: str | None) -> Any:
    if path is None or path == "$":
        return value
    current = value
    normalized = path.removeprefix("$.")
    for token in normalized.split("."):
        if isinstance(current, Mapping):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list) and token.isdecimal():
            index = int(token)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current
