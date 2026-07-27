from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import idna

_TRACKING_KEYS = frozenset(
    {
        "_ga",
        "_gl",
        "dclid",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "utm_campaign",
        "utm_content",
        "utm_id",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)
_WECHAT_TRACKING_KEYS = frozenset(
    {"scene", "from", "subscene", "clicktime", "enterid", "ascene"}
)
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


@dataclass(frozen=True, slots=True)
class DocumentFingerprint:
    canonical_url: str
    content_hash: str
    content_group_hash: str


def normalize_text(text: str) -> str:
    """Return a deterministic NFC representation without changing the language."""

    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in normalized.split("\n")]
    compacted: list[str] = []
    previous_blank = True
    for line in lines:
        if line:
            compacted.append(line)
            previous_blank = False
        elif not previous_blank:
            compacted.append("")
            previous_blank = True
    while compacted and not compacted[-1]:
        compacted.pop()
    return "\n".join(compacted)


def canonicalize_url(url: str) -> str:
    """Build a stable HTTP(S) identity while retaining business query parameters."""

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("URL is malformed") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("URL must be absolute HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL user information is not allowed")

    host = _canonical_host(parsed.hostname)
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL port is out of range")
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"

    path = _canonical_path(parsed.path or "/")
    tracking_keys = _TRACKING_KEYS
    if host == "mp.weixin.qq.com" and path.startswith("/s/"):
        tracking_keys = tracking_keys | _WECHAT_TRACKING_KEYS
    retained = [
        (unicodedata.normalize("NFC", key), unicodedata.normalize("NFC", value))
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in tracking_keys
    ]
    retained.sort(key=lambda pair: (pair[0], pair[1]))
    query = urlencode(retained, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def content_hash(body: str) -> str:
    return hashlib.sha256(normalize_text(body).encode("utf-8")).hexdigest()


def content_group_hash(body: str) -> str:
    return content_hash(body)


def fingerprint_document(url: str, body: str) -> DocumentFingerprint:
    digest = content_hash(body)
    return DocumentFingerprint(
        canonical_url=canonicalize_url(url),
        content_hash=digest,
        content_group_hash=digest,
    )


def _canonical_host(host: str) -> str:
    candidate = unicodedata.normalize("NFC", host).rstrip(".").lower()
    if not candidate:
        raise ValueError("URL host is empty")
    try:
        address = ip_address(candidate)
    except ValueError:
        try:
            return idna.encode(
                candidate,
                uts46=True,
                std3_rules=True,
                transitional=False,
            ).decode("ascii")
        except idna.IDNAError as exc:
            raise ValueError("URL host is invalid") from exc
    if address.version == 6:
        return f"[{address.compressed}]"
    return address.compressed


def _canonical_path(path: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(path):
        if path[index] != "%" or _PERCENT_ESCAPE.match(path, index) is None:
            decoded.append("%25" if path[index] == "%" else path[index])
            index += 1
            continue

        raw = bytearray()
        escapes: list[str] = []
        while (match := _PERCENT_ESCAPE.match(path, index)) is not None:
            raw.append(int(match.group(1), 16))
            escapes.append(f"%{match.group(1).upper()}")
            index = match.end()
        try:
            characters = raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded.extend(escapes)
            continue
        for character in characters:
            if ord(character) > 127 or character in _UNRESERVED:
                decoded.append(character)
            else:
                decoded.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))

    nfc_path = unicodedata.normalize("NFC", "".join(decoded))
    return quote(_remove_dot_segments(nfc_path), safe="/%:@!$&'()*+,;=-._~")


def _remove_dot_segments(path: str) -> str:
    """Apply the RFC 3986 section 5.2.4 algorithm without collapsing empty segments."""

    remaining = path
    output = ""
    while remaining:
        if remaining.startswith("../"):
            remaining = remaining[3:]
        elif remaining.startswith("./"):
            remaining = remaining[2:]
        elif remaining.startswith("/./"):
            remaining = remaining[2:]
        elif remaining == "/.":
            remaining = "/"
        elif remaining.startswith("/../"):
            remaining = remaining[3:]
            output = _remove_last_segment(output)
        elif remaining == "/..":
            remaining = "/"
            output = _remove_last_segment(output)
        elif remaining in {".", ".."}:
            remaining = ""
        else:
            next_slash = remaining.find("/", 1 if remaining.startswith("/") else 0)
            if next_slash == -1:
                output += remaining
                remaining = ""
            else:
                output += remaining[:next_slash]
                remaining = remaining[next_slash:]
    return output


def _remove_last_segment(path: str) -> str:
    final_slash = path.rfind("/")
    return path[:final_slash] if final_slash >= 0 else ""
