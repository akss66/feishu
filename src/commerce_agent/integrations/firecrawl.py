"""Async, secret-safe Firecrawl v2 scrape integration."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx
from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class FirecrawlDocument:
    url: str
    markdown: str
    title: str | None = None
    status_code: int | None = None
    cache_state: str | None = None
    cached_at: datetime | None = None


class FirecrawlError(RuntimeError):
    """Stable, secret-free failure classification."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"firecrawl request failed: {code}")


class FirecrawlClient:
    """Call the hosted Firecrawl v2 scrape endpoint without blocking asyncio."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        api_url: str = "https://api.firecrawl.dev",
        timeout_seconds: float = 30.0,
        max_age_ms: int = 900_000,
        max_concurrency: int = 1,
        max_attempts: int = 3,
        min_request_interval_seconds: float = 6.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise ValueError("firecrawl api key must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_age_ms < 0:
            raise ValueError("max_age_ms must not be negative")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds must not be negative")
        self._api_key = api_key
        self._max_age_ms = max_age_ms
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._monotonic = monotonic
        self._min_request_interval_seconds = min_request_interval_seconds
        self._last_request_started_at: float | None = None
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def scrape(self, url: str) -> FirecrawlDocument:
        async with self._semaphore:
            response = await self._post_with_retries(url)

        if response.status_code in {401, 403}:
            raise FirecrawlError("firecrawl_auth_failed")
        if response.status_code == 429:
            raise FirecrawlError("firecrawl_rate_limited")
        if response.status_code >= 500:
            raise FirecrawlError("firecrawl_service_error")
        if not 200 <= response.status_code < 300:
            raise FirecrawlError("firecrawl_request_failed")

        try:
            payload = response.json()
        except ValueError as error:
            raise FirecrawlError("firecrawl_invalid_response") from error
        return _document_from_payload(payload, requested_url=url)

    async def _post_with_retries(self, url: str) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._wait_for_request_slot()
            try:
                response = await self._http.post(
                    "/v2/scrape",
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "url": url,
                        "formats": ["markdown"],
                        "onlyMainContent": True,
                        "maxAge": self._max_age_ms,
                    },
                )
            except httpx.RequestError as error:
                raise FirecrawlError("firecrawl_transport_error") from error

            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                return response
            if attempt == self._max_attempts:
                return response
            await self._sleep(_retry_delay_seconds(response, attempt=attempt))

        if response is None:  # pragma: no cover - constructor validation makes this unreachable
            raise FirecrawlError("firecrawl_request_failed")
        return response

    async def _wait_for_request_slot(self) -> None:
        if self._last_request_started_at is not None:
            elapsed = self._monotonic() - self._last_request_started_at
            remaining = self._min_request_interval_seconds - elapsed
            if remaining > 0:
                await self._sleep(remaining)
        self._last_request_started_at = self._monotonic()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()


def _retry_delay_seconds(response: httpx.Response, *, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass
    return min(2.0**attempt, 30.0) + random.uniform(0.0, 1.0)


def _document_from_payload(payload: object, *, requested_url: str) -> FirecrawlDocument:
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        raise FirecrawlError("firecrawl_invalid_response")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise FirecrawlError("firecrawl_invalid_response")
    markdown = data.get("markdown")
    metadata = data.get("metadata")
    if not isinstance(markdown, str) or not markdown.strip() or not isinstance(metadata, Mapping):
        raise FirecrawlError("firecrawl_invalid_response")

    return FirecrawlDocument(
        url=_optional_string(metadata.get("sourceURL")) or requested_url,
        markdown=markdown,
        title=_optional_string(metadata.get("title")),
        status_code=_optional_status_code(metadata.get("statusCode")),
        cache_state=_optional_string(metadata.get("cacheState")),
        cached_at=_optional_datetime(metadata.get("cachedAt")),
    )


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_status_code(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
