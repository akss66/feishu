from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass
from ipaddress import IPv6Address, ip_address
from types import ModuleType
from typing import Protocol

from commerce_agent.ingestion.collectors.base import (
    BrowserPort,
    BrowserRequest,
    CollectorError,
    RenderedPage,
    allowed_hosts,
    item_limit,
)
from commerce_agent.ingestion.collectors.html import links_from_html
from commerce_agent.ingestion.models import (
    CollectedItem,
    FetchContext,
    ResponseArtifact,
    SourceDefinition,
)
from commerce_agent.ingestion.security import SafeUrl, UrlSafetyError, UrlSafetyPolicy


class _RouteRequest(Protocol):
    url: str


class _Route(Protocol):
    request: _RouteRequest

    async def abort(self, error_code: str) -> None: ...

    async def continue_(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _PinnedHost:
    addresses: frozenset[str]
    chromium_address: str


class PlaywrightBrowserPort:
    """Optional renderer loaded only when browser collection is invoked."""

    def __init__(self, *, safety_policy: UrlSafetyPolicy | None = None) -> None:
        self._safety_policy = safety_policy or UrlSafetyPolicy()

    async def render(self, request: BrowserRequest) -> RenderedPage:
        entry_url, pinned_hosts = await self._prepare_pins(request)
        module = _load_playwright()
        timeout_ms = request.timeout_seconds * 1000
        resolver_rules = _chromium_resolver_rules(pinned_hosts)
        try:
            async with module.async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=["--disable-quic", f"--host-resolver-rules={resolver_rules}"],
                )
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
                        try:
                            await self._validate_pinned(
                                route.request.url,
                                request.allowed_hosts,
                                pinned_hosts,
                            )
                        except CollectorError:
                            await route.abort("blockedbyclient")
                        else:
                            await route.continue_()

                    await page.route("**/*", guard_route)
                    navigation_response = await page.goto(
                        entry_url.url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    if navigation_response is None:
                        raise CollectorError("renderer_response_unavailable")
                    try:
                        final_url = await self._validate_pinned(
                            page.url,
                            request.allowed_hosts,
                            pinned_hosts,
                        )
                    except CollectorError:
                        request.metrics.record_response(
                            status_code=navigation_response.status,
                            bytes_received=0,
                        )
                        raise
                    try:
                        raw_headers = await navigation_response.all_headers()
                        raw_body = await navigation_response.body()
                        artifact = ResponseArtifact(
                            url=final_url.url,
                            status_code=navigation_response.status,
                            headers=raw_headers,
                            body=raw_body,
                        )
                    except Exception:
                        request.metrics.record_response(
                            status_code=navigation_response.status,
                            bytes_received=0,
                        )
                        raise CollectorError("renderer_response_unavailable") from None
                    request.metrics.record_response(
                        status_code=artifact.status_code,
                        bytes_received=len(artifact.body),
                    )
                    body = (await page.content()).encode("utf-8")
                    return RenderedPage(
                        url=final_url.url,
                        body=body,
                        artifact=artifact,
                        headers=dict(artifact.headers),
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

    async def _prepare_pins(
        self,
        request: BrowserRequest,
    ) -> tuple[SafeUrl, dict[str, _PinnedHost]]:
        pinned_hosts: dict[str, _PinnedHost] = {}
        for allowed_host in request.allowed_hosts:
            safe_host = await self._validate(
                _host_validation_url(allowed_host),
                request.allowed_hosts,
            )
            pin = _pin(safe_host)
            existing = pinned_hosts.get(safe_host.host)
            if existing is not None and existing != pin:
                raise CollectorError("renderer_security_rejected")
            pinned_hosts[safe_host.host] = pin
        if not pinned_hosts:
            raise CollectorError("renderer_security_rejected")
        entry_url = await self._validate_pinned(
            request.url,
            request.allowed_hosts,
            pinned_hosts,
        )
        return entry_url, pinned_hosts

    async def _validate_pinned(
        self,
        url: str,
        allowed_hosts: Collection[str],
        pinned_hosts: dict[str, _PinnedHost],
    ) -> SafeUrl:
        safe_url = await self._validate(url, allowed_hosts)
        expected = pinned_hosts.get(safe_url.host)
        if expected is None or expected.addresses != frozenset(safe_url.resolved_addresses):
            raise CollectorError("renderer_security_rejected")
        return safe_url

    async def _validate(self, url: str, allowed_hosts: Collection[str]) -> SafeUrl:
        try:
            return await self._safety_policy.validate(url, allowed_hosts)
        except UrlSafetyError:
            raise CollectorError("renderer_security_rejected") from None


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
        if not self._enabled:
            raise CollectorError("renderer_unavailable")
        browser = self._browser or PlaywrightBrowserPort()
        page = await browser.render(
            BrowserRequest(
                url=source.entry_url,
                allowed_hosts=allowed_hosts(source),
                timeout_seconds=self._timeout_seconds,
                metrics=context.metrics,
            )
        )
        _require_render_success(page)
        selector = source.collector_config.get("link_selector")
        if not isinstance(selector, str):
            raise CollectorError("invalid_config")
        for candidate in links_from_html(
            page.body,
            base_url=page.url,
            selector=selector,
            limit=item_limit(source),
        ):
            detail = await browser.render(
                BrowserRequest(
                    url=candidate.url,
                    allowed_hosts=allowed_hosts(source),
                    timeout_seconds=self._timeout_seconds,
                    metrics=context.metrics,
                )
            )
            _require_render_success(detail)
            yield CollectedItem(
                url=detail.url,
                body=detail.body,
                content_type=detail.artifact.headers.get("content-type") or "text/html",
                title=candidate.title,
                etag=detail.artifact.headers.get("etag"),
                last_modified=detail.artifact.headers.get("last-modified"),
                artifact=detail.artifact,
            )


def _require_render_success(page: RenderedPage) -> None:
    if not 200 <= page.artifact.status_code < 300:
        raise CollectorError("fetch_failed")


def _load_playwright() -> ModuleType:
    try:
        return importlib.import_module("playwright.async_api")
    except (ImportError, ModuleNotFoundError):
        raise CollectorError("renderer_unavailable") from None


def _pin(safe_url: SafeUrl) -> _PinnedHost:
    addresses = frozenset(safe_url.resolved_addresses)
    selected = min(
        addresses,
        key=lambda address: (ip_address(address).version, ip_address(address)),
    )
    parsed = ip_address(selected)
    chromium_address = f"[{parsed}]" if isinstance(parsed, IPv6Address) else str(parsed)
    return _PinnedHost(addresses=addresses, chromium_address=chromium_address)


def _chromium_resolver_rules(pinned_hosts: dict[str, _PinnedHost]) -> str:
    mappings = [
        f"MAP {host} {pinned_hosts[host].chromium_address}"
        for host in sorted(pinned_hosts)
    ]
    mappings.append("MAP * ^NOTFOUND")
    return ", ".join(mappings)


def _host_validation_url(host: str) -> str:
    raw_host = host.rstrip(".")
    try:
        address = ip_address(raw_host.strip("[]"))
    except ValueError:
        rendered_host = raw_host
    else:
        rendered_host = f"[{address}]" if isinstance(address, IPv6Address) else str(address)
    return f"https://{rendered_host}/"
