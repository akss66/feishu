from __future__ import annotations

import json
from ipaddress import ip_address
from typing import Any

import httpx

_DOH_BASE_URL = "https://1.1.1.1"
_DOH_PATH = "/dns-query"
_DOH_CONTENT_TYPE = "application/dns-json"
_DNS_RECORD_TYPES = (("A", 1), ("AAAA", 28))


class DohResolutionError(RuntimeError):
    """A deliberately opaque failure from the external DNS resolver."""


class CloudflareDohResolver:
    """Resolve A and AAAA records through Cloudflare's fixed DoH endpoint."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 65_536,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes
        self._closed = False
        self._client = httpx.AsyncClient(
            base_url=_DOH_BASE_URL,
            headers={"Accept": _DOH_CONTENT_TYPE},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def __call__(self, host: str) -> tuple[str, ...]:
        try:
            addresses: list[str] = []
            for record_name, record_type in _DNS_RECORD_TYPES:
                addresses.extend(await self._query(host, record_name, record_type))
            deduplicated = tuple(dict.fromkeys(addresses))
            if not deduplicated:
                raise ValueError("empty DNS result")
            return deduplicated
        except Exception:
            raise DohResolutionError("dns_resolution_failed") from None

    async def _query(self, host: str, record_name: str, record_type: int) -> tuple[str, ...]:
        async with self._client.stream(
            "GET",
            _DOH_PATH,
            params={"name": host, "type": record_name},
        ) as response:
            if response.status_code != 200:
                raise ValueError("unexpected DNS status")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != _DOH_CONTENT_TYPE:
                raise ValueError("unexpected DNS content type")
            body = await self._read_bounded(response)

        payload = json.loads(body)
        return self._parse_payload(payload, record_type)

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise ValueError("DNS response exceeds limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse_payload(payload: Any, expected_type: int) -> tuple[str, ...]:
        if not isinstance(payload, dict):
            raise ValueError("invalid DNS payload")
        status = payload.get("Status")
        if isinstance(status, bool) or not isinstance(status, int) or status != 0:
            raise ValueError("DNS query failed")
        if payload.get("TC", False) is not False:
            raise ValueError("truncated DNS response")

        answers = payload.get("Answer", [])
        if not isinstance(answers, list):
            raise ValueError("invalid DNS answers")

        addresses: list[str] = []
        for answer in answers:
            if not isinstance(answer, dict):
                raise ValueError("invalid DNS answer")
            answer_type = answer.get("type")
            if isinstance(answer_type, bool) or not isinstance(answer_type, int):
                raise ValueError("invalid DNS answer type")
            if answer_type != expected_type:
                continue
            value = answer.get("data")
            if not isinstance(value, str):
                raise ValueError("invalid DNS address")
            parsed = ip_address(value)
            if parsed.version != (4 if expected_type == 1 else 6):
                raise ValueError("mismatched DNS address family")
            addresses.append(str(parsed))
        return tuple(addresses)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()
