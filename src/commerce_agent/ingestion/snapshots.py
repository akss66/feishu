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
_SNAPSHOT_NAME = re.compile(r"^[a-f0-9]{64}\.bin\.gz$")


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

    async def prune_source_before(self, source_id: str, cutoff: datetime) -> int:
        """Delete dated raw snapshots for one exact source before ``cutoff``."""

        if not _SOURCE_ID.fullmatch(source_id):
            raise SnapshotStoreError("invalid_source_id")
        if cutoff.tzinfo is None:
            raise ValueError("snapshot cutoff must be timezone-aware")
        async with self._lock:
            return await asyncio.to_thread(
                self._prune_source_before,
                source_id,
                cutoff.astimezone(UTC),
            )

    def _prune_source_before(self, source_id: str, cutoff: datetime) -> int:
        if not self._root.exists():
            return 0
        removed = 0
        for year_dir in self._root.iterdir():
            if not year_dir.is_dir() or not year_dir.name.isdecimal():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir() or not month_dir.name.isdecimal():
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir() or not day_dir.name.isdecimal():
                        continue
                    try:
                        snapshot_date = datetime(
                            int(year_dir.name),
                            int(month_dir.name),
                            int(day_dir.name),
                            tzinfo=UTC,
                        ).date()
                    except ValueError:
                        continue
                    if snapshot_date >= cutoff.date():
                        continue
                    source_dir = day_dir / source_id
                    if not source_dir.is_dir():
                        continue
                    try:
                        source_dir.resolve().relative_to(self._root)
                    except ValueError:
                        raise SnapshotStoreError("path_outside_root") from None
                    for candidate in source_dir.iterdir():
                        if not candidate.is_file() or not _SNAPSHOT_NAME.fullmatch(
                            candidate.name
                        ):
                            continue
                        try:
                            candidate.resolve().relative_to(self._root)
                        except ValueError:
                            raise SnapshotStoreError("path_outside_root") from None
                        candidate.unlink()
                        removed += 1
        return removed

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
