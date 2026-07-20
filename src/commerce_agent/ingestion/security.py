"""Dynamic URL safety and log-redaction boundaries."""

import asyncio
import re
import socket
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from urllib.parse import SplitResult, urlsplit, urlunsplit

Resolver = Callable[[str], Awaitable[Collection[str]]]

_METADATA_HOSTS = frozenset(
    {
        "instance-data.ec2.internal",
        "metadata.aws.internal",
        "metadata.azure.internal",
        "metadata.google.internal",
        "metadata.goog",
    }
)
_METADATA_IPS = frozenset(
    {
        "100.100.100.200",
        "169.254.169.254",
        "169.254.170.2",
    }
)
_URL_LIKE = re.compile(r"(?i)\b(?:https?://[^\s<>\"']+|(?:data|file):[^\s<>\"']+)")
_SENSITIVE_HEADER = re.compile(
    r"(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key)"
    r"\b[\"']?\s*:\s*)[^\r\n]*"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|id_token|api[_-]?key|token|secret|password)"
    r"\b[\"']?\s*=\s*[\"']?)[^&,;\s}\]]+"
)
_BEARER_TOKEN = re.compile(r"(?i)(\bbearer\s+)[^,;\s}\]]+")


class UrlSafetyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"URL rejected: {code}")


@dataclass(frozen=True, slots=True)
class SafeUrl:
    url: str
    host: str
    resolved_addresses: tuple[str, ...]


class UrlSafetyPolicy:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or _resolve_all

    async def validate(self, url: str, allowed_hosts: Collection[str]) -> SafeUrl:
        """Validate an initial URL or redirect target before each request."""
        parsed = _parse_url(url)
        if parsed.scheme.lower() != "https":
            raise UrlSafetyError("scheme_not_allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UrlSafetyError("userinfo_not_allowed")
        try:
            port = parsed.port
        except ValueError:
            raise UrlSafetyError("invalid_url") from None
        if port not in {None, 443}:
            raise UrlSafetyError("port_not_allowed")

        host = _normalize_host(parsed.hostname)
        normalized_allowed_hosts = {
            normalized
            for candidate in allowed_hosts
            if (normalized := _normalize_host(candidate, required=False)) is not None
        }
        if host not in normalized_allowed_hosts:
            raise UrlSafetyError("host_not_allowed")
        if _is_forbidden_hostname(host):
            raise UrlSafetyError("destination_not_public")

        literal_address = _as_ip_address(host)
        if literal_address is not None:
            addresses = (literal_address,)
        else:
            try:
                resolved = tuple(await self._resolver(host))
            except Exception:
                raise UrlSafetyError("dns_resolution_failed") from None
            if not resolved:
                raise UrlSafetyError("dns_resolution_failed")
            try:
                addresses = tuple(ip_address(address) for address in resolved)
            except ValueError:
                raise UrlSafetyError("dns_resolution_failed") from None

        if any(not _is_public_address(address) for address in addresses):
            raise UrlSafetyError("destination_not_public")

        netloc = f"[{host}]" if isinstance(literal_address, IPv6Address) else host
        normalized_url = urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))
        return SafeUrl(
            url=normalized_url,
            host=host,
            resolved_addresses=tuple(str(address) for address in addresses),
        )

    def redact_for_log(self, url: object) -> str:
        """Render URL-bearing or credential-bearing text without logging secrets."""
        text = str(url)
        if _is_entire_url(text):
            return _render_url_for_log(text)
        text = _URL_LIKE.sub(lambda match: _render_url_for_log(match.group(0)), text)
        text = _SENSITIVE_HEADER.sub(r"\1[REDACTED]", text)
        text = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", text)
        return _BEARER_TOKEN.sub(r"\1[REDACTED]", text)


def _parse_url(url: str) -> SplitResult:
    if not isinstance(url, str) or not url or any(character.isspace() for character in url):
        raise UrlSafetyError("invalid_url")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise UrlSafetyError("invalid_url") from None
    if parsed.hostname is None:
        if parsed.scheme.lower() != "https":
            return parsed
        raise UrlSafetyError("invalid_url")
    return parsed


def _normalize_host(host: str | None, *, required: bool = True) -> str | None:
    if host is None:
        if required:
            raise UrlSafetyError("invalid_url")
        return None
    normalized = host.rstrip(".").lower()
    if not normalized:
        if required:
            raise UrlSafetyError("invalid_url")
        return None
    address = _as_ip_address(normalized)
    if address is not None:
        return str(address)
    try:
        ascii_host = normalized.encode("idna").decode("ascii")
    except UnicodeError:
        if required:
            raise UrlSafetyError("invalid_url") from None
        return None
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or re.fullmatch(r"[a-z0-9-]+", label) is None
        for label in labels
    ):
        if required:
            raise UrlSafetyError("invalid_url")
        return None
    return ascii_host


def _as_ip_address(host: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(host)
    except ValueError:
        return None


def _is_forbidden_hostname(host: str) -> bool:
    return (
        host == "localhost"
        or host.endswith(".localhost")
        or host in _METADATA_HOSTS
        or host in _METADATA_IPS
    )


def _is_public_address(address: IPv4Address | IPv6Address) -> bool:
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address) not in _METADATA_IPS and address.is_global and not address.is_multicast


async def _resolve_all(host: str) -> Collection[str]:
    loop = asyncio.get_running_loop()
    results = await loop.getaddrinfo(
        host,
        443,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(dict.fromkeys(result[4][0] for result in results))


def _is_entire_url(text: str) -> bool:
    match = _URL_LIKE.fullmatch(text)
    return match is not None


def _render_url_for_log(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "[REDACTED_URL]"
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        return "[REDACTED_URL]"
    try:
        host = _normalize_host(parsed.hostname)
    except UrlSafetyError:
        return "[REDACTED_URL]"
    netloc = f"[{host}]" if _as_ip_address(host) and ":" in host else host
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))
