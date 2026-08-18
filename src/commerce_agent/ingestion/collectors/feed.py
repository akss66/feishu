from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from commerce_agent.ingestion.collectors.base import (
    CollectorError,
    HttpPort,
    candidate_url,
    detail_failure,
    fetch_request,
    item_limit,
    require_success,
    response_artifact,
)
from commerce_agent.ingestion.http import FetchError
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    FetchContext,
    SourceDefinition,
)
from commerce_agent.ingestion.security import UrlSafetyError


class FeedCollector:
    def __init__(self, http_port: HttpPort) -> None:
        self._http = http_port

    async def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem | CollectedFailure]:
        response = await self._http.get(fetch_request(source, context))
        if not require_success(response):
            return
        artifact = response_artifact(response)
        try:
            root = ElementTree.fromstring(response.body)
        except ElementTree.ParseError:
            raise CollectorError("invalid_payload") from None

        root_name = _local_name(root.tag)
        if root_name == "rss":
            entries = [element for element in root.iter() if _local_name(element.tag) == "item"]
            atom = False
        elif root_name == "feed":
            entries = [element for element in root if _local_name(element.tag) == "entry"]
            atom = True
        else:
            raise CollectorError("invalid_payload")

        seen: set[str] = set()
        limit = item_limit(source)
        entry_match_terms = _match_terms(source, "entry_match_terms")
        detail_match_terms = _match_terms(source, "detail_match_terms")
        for entry in entries:
            raw_link = _atom_link(entry) if atom else _child_text(entry, "link")
            url = candidate_url(response.url, raw_link)
            if url is None or url in seen:
                continue
            seen.add(url)
            reached_limit = len(seen) >= limit
            summary = _child_text(entry, "summary") or _child_text(entry, "description") or ""
            published = (
                _child_text(entry, "published")
                or _child_text(entry, "updated")
                or _child_text(entry, "pubDate")
            )
            author = _author(entry)
            if entry_match_terms:
                entry_text = " ".join(
                    part
                    for part in (
                        _child_text(entry, "title"),
                        summary,
                        author,
                        url,
                    )
                    if part
                ).casefold()
                if not any(term in entry_text for term in entry_match_terms):
                    if reached_limit:
                        return
                    continue
            body = summary.encode("utf-8")
            content_type = "text/html" if summary else None
            item_etag = response.etag
            item_last_modified = response.last_modified
            item_artifact = artifact
            if detail_match_terms:
                try:
                    detail = await self._http.get(
                        fetch_request(source, context, url=url, conditional=False)
                    )
                    if not require_success(detail):
                        yield CollectedFailure("detail_fetch_failed")
                        if reached_limit:
                            return
                        continue
                except asyncio.CancelledError:
                    raise
                except (FetchError, CollectorError, UrlSafetyError) as error:
                    yield detail_failure(error)
                    if reached_limit:
                        return
                    continue
                detail_text = detail.body.decode("utf-8", errors="replace").casefold()
                if not any(term in detail_text for term in detail_match_terms):
                    if reached_limit:
                        return
                    continue
                body = detail.body
                content_type = detail.headers.get("content-type")
                item_etag = detail.etag
                item_last_modified = detail.last_modified
                item_artifact = response_artifact(detail)
            yield CollectedItem(
                url=url,
                body=body,
                content_type=content_type,
                title=_child_text(entry, "title"),
                author=author,
                published_at=_parse_datetime(published),
                etag=item_etag,
                last_modified=item_last_modified,
                artifact=item_artifact,
            )
            if reached_limit:
                return


def _match_terms(source: SourceDefinition, name: str) -> tuple[str, ...]:
    value = source.collector_config.get(name)
    if value is None:
        return ()
    if not isinstance(value, str):
        raise CollectorError("invalid_config")
    terms = tuple(term.strip().casefold() for term in value.split("|") if term.strip())
    if not terms:
        raise CollectorError("invalid_config")
    return terms


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(entry: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((child for child in entry if _local_name(child.tag) == name), None)


def _child_text(entry: ElementTree.Element, name: str) -> str | None:
    child = _child(entry, name)
    if child is None:
        return None
    value = "".join(child.itertext()).strip()
    return value or None


def _atom_link(entry: ElementTree.Element) -> str | None:
    links = [child for child in entry if _local_name(child.tag) == "link"]
    preferred = next(
        (link for link in links if link.attrib.get("rel", "alternate") == "alternate"),
        None,
    )
    link = preferred if preferred is not None else (links[0] if links else None)
    return link.attrib.get("href") if link is not None else None


def _author(entry: ElementTree.Element) -> str | None:
    author = _child(entry, "author")
    if author is None:
        return _child_text(entry, "creator")
    name = _child_text(author, "name")
    return name or ("".join(author.itertext()).strip() or None)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
