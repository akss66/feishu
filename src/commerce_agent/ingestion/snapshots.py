"""Content-addressed, gzip-compressed response snapshot storage."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from commerce_agent.ingestion.http import FetchResponse

_SOURCE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    relative_path: str
    sha256: str
    media_type: str | None
    byte_count: int


class SnapshotStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"snapshot storage failed: {code}")


class SnapshotStore:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._root = Path(root).resolve()
        self._clock = clock
        self._lock = asyncio.Lock()

    async def save(self, source_id: str, response: FetchResponse) -> SnapshotRef:
        if not _SOURCE_ID.fullmatch(source_id):
            raise SnapshotStoreError("invalid_source_id")
        saved_at = self._clock()
        body = bytes(response.body)
        digest = hashlib.sha256(body).hexdigest()
        relative_path = Path(
            f"{saved_at.year:04d}",
            f"{saved_at.month:02d}",
            f"{saved_at.day:02d}",
            source_id,
            f"{digest}.bin.gz",
        )
        reference = SnapshotRef(
            relative_path=relative_path.as_posix(),
            sha256=digest,
            media_type=_media_type(response.headers),
            byte_count=len(body),
        )
        async with self._lock:
            await asyncio.to_thread(self._save_atomic, relative_path, body)
        return reference

    def _save_atomic(self, relative_path: Path, body: bytes) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root.joinpath(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.resolve().relative_to(self._root)
        except ValueError:
            raise SnapshotStoreError("path_outside_root") from None

        if target.exists():
            _require_matching_snapshot(target, body)
            return

        compressed = gzip.compress(body, compresslevel=9, mtime=0)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.stem}-",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(compressed)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists():
                _require_matching_snapshot(target, body)
                return
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def _require_matching_snapshot(path: Path, expected: bytes) -> None:
    try:
        actual = gzip.decompress(path.read_bytes())
    except (OSError, EOFError):
        raise SnapshotStoreError("hash_path_conflict") from None
    if actual != expected:
        raise SnapshotStoreError("hash_path_conflict")


def _media_type(headers: Mapping[str, str]) -> str | None:
    raw_content_type = next(
        (value for key, value in headers.items() if key.lower() == "content-type"),
        None,
    )
    if raw_content_type is None:
        return None
    media_type = raw_content_type.partition(";")[0].strip().lower()
    return media_type if _MEDIA_TYPE.fullmatch(media_type) else None
