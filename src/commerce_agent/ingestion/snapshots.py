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
from pathlib import Path, PurePosixPath

from commerce_agent.ingestion.http import FetchResponse

_SOURCE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_LEGACY_SNAPSHOT_NAME = re.compile(r"^[a-f0-9]{64}\.bin\.gz$")
_TIMESTAMPED_SNAPSHOT_NAME = re.compile(
    r"^(?P<created_at>\d{8}T\d{12}Z)-"
    r"(?P<sha256>[a-f0-9]{64})\."
    r"(?P<retention>archive|temporary)\.bin\.gz$"
)
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    relative_path: str
    sha256: str
    media_type: str | None
    byte_count: int
    created_at: datetime | None = None


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

    async def save(
        self,
        source_id: str,
        response: FetchResponse,
        *,
        temporary: bool = False,
    ) -> SnapshotRef:
        if not _SOURCE_ID.fullmatch(source_id):
            raise SnapshotStoreError("invalid_source_id")
        saved_at = _require_utc(self._clock(), code="invalid_snapshot_timestamp")
        body = bytes(response.body)
        digest = hashlib.sha256(body).hexdigest()
        retention = "temporary" if temporary else "archive"
        name = f"{saved_at.strftime(_TIMESTAMP_FORMAT)}-{digest}.{retention}.bin.gz"
        relative_path = Path(
            f"{saved_at.year:04d}",
            f"{saved_at.month:02d}",
            f"{saved_at.day:02d}",
            source_id,
            name,
        )
        reference = SnapshotRef(
            relative_path=relative_path.as_posix(),
            sha256=digest,
            media_type=_media_type(response.headers),
            byte_count=len(body),
            created_at=saved_at,
        )
        async with self._lock:
            await asyncio.to_thread(self._save_atomic, relative_path, body)
        return reference

    async def prune_source_before(self, source_id: str, cutoff: datetime) -> int:
        """Delete dated raw snapshots for one exact source before ``cutoff``."""

        if not _SOURCE_ID.fullmatch(source_id):
            raise SnapshotStoreError("invalid_source_id")
        normalized_cutoff = _require_utc(cutoff, code=None)
        async with self._lock:
            return await asyncio.to_thread(
                self._prune_before,
                normalized_cutoff,
                frozenset({source_id}),
                frozenset({source_id}),
                frozenset(),
                True,
            )

    async def prune_before(
        self,
        cutoff: datetime,
        *,
        legacy_temporary_source_ids: tuple[str, ...] = (),
        legacy_temporary_paths: tuple[str, ...] = (),
    ) -> int:
        """Delete all expired temporary snapshots using exact UTC timestamps."""

        normalized_cutoff = _require_utc(cutoff, code=None)
        legacy_ids = frozenset(legacy_temporary_source_ids)
        if any(not _SOURCE_ID.fullmatch(source_id) for source_id in legacy_ids):
            raise SnapshotStoreError("invalid_source_id")
        legacy_paths = frozenset(
            _normalize_snapshot_path(path) for path in legacy_temporary_paths
        )
        async with self._lock:
            return await asyncio.to_thread(
                self._prune_before,
                normalized_cutoff,
                None,
                legacy_ids,
                legacy_paths,
                False,
            )

    def _prune_before(
        self,
        cutoff: datetime,
        source_filter: frozenset[str] | None,
        legacy_temporary_source_ids: frozenset[str],
        legacy_temporary_paths: frozenset[str],
        include_archives: bool,
    ) -> int:
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
                        snapshot_day = datetime(
                            int(year_dir.name),
                            int(month_dir.name),
                            int(day_dir.name),
                            tzinfo=UTC,
                        )
                    except ValueError:
                        continue
                    for source_dir in day_dir.iterdir():
                        if (
                            not source_dir.is_dir()
                            or not _SOURCE_ID.fullmatch(source_dir.name)
                            or source_filter is not None
                            and source_dir.name not in source_filter
                        ):
                            continue
                        try:
                            source_dir.resolve().relative_to(self._root)
                        except ValueError:
                            raise SnapshotStoreError("path_outside_root") from None
                        for candidate in source_dir.iterdir():
                            if not candidate.is_file():
                                continue
                            try:
                                candidate.resolve().relative_to(self._root)
                            except ValueError:
                                raise SnapshotStoreError("path_outside_root") from None
                            relative_path = candidate.relative_to(self._root).as_posix()
                            explicitly_temporary = (
                                relative_path in legacy_temporary_paths
                            )
                            created_at: datetime | None = None
                            timestamped = _TIMESTAMPED_SNAPSHOT_NAME.fullmatch(
                                candidate.name
                            )
                            if timestamped is not None:
                                if (
                                    not include_archives
                                    and timestamped["retention"] != "temporary"
                                    and not explicitly_temporary
                                ):
                                    continue
                                created_at = _parse_created_at(timestamped["created_at"])
                            elif (
                                _LEGACY_SNAPSHOT_NAME.fullmatch(candidate.name)
                                and (
                                    include_archives
                                    or source_dir.name in legacy_temporary_source_ids
                                    or explicitly_temporary
                                )
                            ):
                                # Legacy paths contain only a UTC calendar day. Use
                                # the earliest possible instant so unknown precision
                                # can never extend retention beyond seven days.
                                created_at = snapshot_day
                            if created_at is None or created_at >= cutoff:
                                continue
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
            prefix=".snapshot-",
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


def _require_utc(value: datetime, *, code: str | None) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        if code is not None:
            raise SnapshotStoreError(code)
        raise ValueError("snapshot cutoff must be timezone-aware")
    return value.astimezone(UTC)


def _parse_created_at(value: str) -> datetime:
    try:
        return datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        raise SnapshotStoreError("invalid_snapshot_timestamp") from None


def _normalize_snapshot_path(value: str) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise SnapshotStoreError("invalid_snapshot_path")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 5 or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise SnapshotStoreError("invalid_snapshot_path")
    year, month, day, source_id, name = path.parts
    if (
        len(year) != 4
        or len(month) != 2
        or len(day) != 2
        or not year.isdecimal()
        or not month.isdecimal()
        or not day.isdecimal()
        or not _SOURCE_ID.fullmatch(source_id)
        or not (
            _LEGACY_SNAPSHOT_NAME.fullmatch(name)
            or _TIMESTAMPED_SNAPSHOT_NAME.fullmatch(name)
        )
    ):
        raise SnapshotStoreError("invalid_snapshot_path")
    return path.as_posix()
