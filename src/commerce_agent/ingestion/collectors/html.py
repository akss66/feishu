from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

from commerce_agent.ingestion.collectors.base import (
    CollectorError,
    HttpPort,
    candidate_url,
    fetch_request,
    item_limit,
    require_success,
    response_artifact,
)
from commerce_agent.ingestion.models import CollectedItem, FetchContext, SourceDefinition

_SELECTOR_PART = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*|\*)?(?P<suffix>(?:[.#][\w-]+)*)$")
_SELECTOR_SUFFIX = re.compile(r"([.#])([\w-]+)")
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None
    children: list[_Node] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {}, None)
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.current)
        self.current.children.append(node)
        if tag.lower() not in _VOID_ELEMENTS:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        target = tag.lower()
        node = self.current
        while node.parent is not None:
            if node.tag == target:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data: str) -> None:
        self.current.text.append(data)


class HtmlCollector:
    def __init__(self, http_port: HttpPort) -> None:
        self._http = http_port

    async def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem]:
        response = await self._http.get(fetch_request(source, context))
        if not require_success(response):
            return
        selector = source.collector_config.get("link_selector")
        if not isinstance(selector, str):
            raise CollectorError("invalid_config")
        for candidate in links_from_html(
            response.body,
            base_url=response.url,
            selector=selector,
            limit=item_limit(source),
        ):
            detail = await self._http.get(
                fetch_request(source, context, url=candidate.url, conditional=False)
            )
            if not require_success(detail):
                continue
            artifact = response_artifact(detail)
            yield CollectedItem(
                url=detail.url,
                body=detail.body,
                content_type=artifact.headers.get("content-type"),
                title=candidate.title,
                etag=detail.etag,
                last_modified=detail.last_modified,
                artifact=artifact,
            )


def links_from_html(
    body: bytes,
    *,
    base_url: str,
    selector: str,
    limit: int,
) -> list[CollectedItem]:
    selector_parts = _parse_selector(selector)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    parser = _TreeParser()
    parser.feed(text)
    parser.close()

    items: list[CollectedItem] = []
    seen: set[str] = set()
    for node in _walk(parser.root):
        if not _matches_chain(node, selector_parts):
            continue
        url = candidate_url(base_url, node.attrs.get("href"))
        if url is None or url in seen:
            continue
        seen.add(url)
        title = " ".join(_node_text(node).split()) or None
        items.append(CollectedItem(url=url, body=b"", title=title))
        if len(items) >= limit:
            break
    return items


def _parse_selector(selector: str) -> tuple[tuple[str | None, str | None, frozenset[str]], ...]:
    raw_parts = selector.split()
    if not raw_parts:
        raise CollectorError("invalid_config")
    parsed: list[tuple[str | None, str | None, frozenset[str]]] = []
    for raw in raw_parts:
        match = _SELECTOR_PART.fullmatch(raw)
        if match is None:
            raise CollectorError("invalid_config")
        tag = match.group("tag")
        element_id: str | None = None
        classes: set[str] = set()
        for prefix, value in _SELECTOR_SUFFIX.findall(match.group("suffix")):
            if prefix == "#":
                if element_id is not None:
                    raise CollectorError("invalid_config")
                element_id = value
            else:
                classes.add(value)
        parsed.append((None if tag in {None, "*"} else tag.lower(), element_id, frozenset(classes)))
    return tuple(parsed)


def _matches_chain(
    node: _Node,
    parts: tuple[tuple[str | None, str | None, frozenset[str]], ...],
) -> bool:
    if not _matches(node, parts[-1]):
        return False
    ancestor = node.parent
    for part in reversed(parts[:-1]):
        while ancestor is not None and not _matches(ancestor, part):
            ancestor = ancestor.parent
        if ancestor is None:
            return False
        ancestor = ancestor.parent
    return True


def _matches(node: _Node, part: tuple[str | None, str | None, frozenset[str]]) -> bool:
    tag, element_id, classes = part
    node_classes = frozenset(node.attrs.get("class", "").split())
    return (
        (tag is None or node.tag == tag)
        and (element_id is None or node.attrs.get("id") == element_id)
        and classes.issubset(node_classes)
    )


def _walk(node: _Node):
    for child in node.children:
        yield child
        yield from _walk(child)


def _node_text(node: _Node) -> str:
    return "".join((*node.text, *(_node_text(child) for child in node.children)))
