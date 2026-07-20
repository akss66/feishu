from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from types import ModuleType
from typing import Protocol
from urllib.parse import urlsplit

from commerce_agent.ingestion.collectors.base import (
    BrowserPort,
    BrowserRequest,
    CollectorError,
    RenderedPage,
    allowed_hosts,
    item_limit,
)
from commerce_agent.ingestion.collectors.html import links_from_html
from commerce_agent.ingestion.models import CollectedItem, FetchContext, SourceDefinition


class _RouteRequest(Protocol):
    url: str


class _Route(Protocol):
    request: _RouteRequest

    async def abort(self, error_code: str) -> None: ...

    async def continue_(self) -> None: ...


class PlaywrightBrowserPort:
    """Optional renderer loaded only when browser collection is invoked."""

    async def render(self, request: BrowserRequest) -> RenderedPage:
        module = _load_playwright()
        timeout_ms = request.timeout_seconds * 1000
        allowed = {host.rstrip(".").lower() for host in request.allowed_hosts}
        try:
            async with module.async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                browser_context = None
                try:
                    browser_context = await browser.new_context(
                        accept_downloads=False,
                        service_workers="block",
                    )
                    page = await browser_context.new_page()
                    page.set_default_timeout(timeout_ms)
                    page.set_default_navigation_timeout(timeout_ms)

                    async def guard_route(route: _Route) -> None:
                        route_request = route.request
                        parsed = urlsplit(route_request.url)
                        blocked = (
                            parsed.scheme.lower() not in {"http", "https"}
                            or parsed.hostname is None
                            or parsed.hostname.rstrip(".").lower() not in allowed
                        )
                        if blocked:
                            await route.abort("blockedbyclient")
                        else:
                            await route.continue_()

                    await page.route("**/*", guard_route)
                    await page.goto(
                        request.url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    body = (await page.content()).encode("utf-8")
                    return RenderedPage(
                        url=page.url,
                        body=body,
                        headers={"content-type": "text/html; charset=utf-8"},
                    )
                finally:
                    if browser_context is not None:
                        await browser_context.close()
                    await browser.close()
        except CollectorError:
            raise
        except Exception as exc:
            code = "renderer_timeout" if type(exc).__name__ == "TimeoutError" else "renderer_failed"
            raise CollectorError(code) from None


class BrowserCollector:
    def __init__(
        self,
        *,
        enabled: bool = False,
        browser_port: BrowserPort | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._enabled = enabled
        self._browser = browser_port
        self._timeout_seconds = timeout_seconds

    async def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem]:
        del context
        if not self._enabled:
            raise CollectorError("renderer_unavailable")
        browser = self._browser or PlaywrightBrowserPort()
        page = await browser.render(
            BrowserRequest(
                url=source.entry_url,
                allowed_hosts=allowed_hosts(source),
                timeout_seconds=self._timeout_seconds,
            )
        )
        selector = source.collector_config.get("link_selector")
        if not isinstance(selector, str):
            raise CollectorError("invalid_config")
        for item in links_from_html(
            page.body,
            base_url=page.url,
            selector=selector,
            limit=item_limit(source),
        ):
            yield item


def _load_playwright() -> ModuleType:
    try:
        return importlib.import_module("playwright.async_api")
    except (ImportError, ModuleNotFoundError):
        raise CollectorError("renderer_unavailable") from None
