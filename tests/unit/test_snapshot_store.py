from __future__ import annotations

import gzip
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from commerce_agent.ingestion.http import FetchResponse
from commerce_agent.ingestion.snapshots import SnapshotStore, SnapshotStoreError

NOW = datetime(2026, 7, 20, 10, 30, tzinfo=UTC)


def response(
    body: bytes = b"raw response bytes",
    *,
    url: str = "https://news.example.com/items",
    headers: dict[str, str] | None = None,
) -> FetchResponse:
    return FetchResponse(
        url=url,
        status_code=200,
        headers=headers or {"Content-Type": "application/json; charset=utf-8"},
        body=body,
    )


async def test_saves_gzip_content_at_sha256_addressed_deterministic_path(tmp_path: Path) -> None:
    body = b"raw response bytes"
    digest = hashlib.sha256(body).hexdigest()
    store = SnapshotStore(tmp_path, clock=lambda: NOW)

    reference = await store.save("amazon-news", response(body))

    assert reference.sha256 == digest
    assert reference.created_at == NOW
    assert reference.relative_path == (
        f"2026/07/20/amazon-news/"
        f"20260720T103000000000Z-{digest}.archive.bin.gz"
    )
    assert reference.byte_count == len(body)
    assert reference.media_type == "application/json"
    assert gzip.decompress((tmp_path / reference.relative_path).read_bytes()) == body


async def test_repeated_save_is_idempotent_and_leaves_no_temp_files(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path, clock=lambda: NOW)

    first = await store.save("amazon-news", response())
    bytes_after_first = (tmp_path / first.relative_path).read_bytes()
    second = await store.save("amazon-news", response())

    assert second == first
    assert (tmp_path / second.relative_path).read_bytes() == bytes_after_first
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [  # noqa: ASYNC240
        tmp_path / first.relative_path
    ]


@pytest.mark.parametrize(
    "source_id",
    ["../outside", "nested/source", r"nested\\source", ".", "..", "C:escape", "Amazon"],
)
async def test_rejects_source_ids_that_could_escape_or_create_ambiguous_paths(
    tmp_path: Path,
    source_id: str,
) -> None:
    store = SnapshotStore(tmp_path, clock=lambda: NOW)

    with pytest.raises(SnapshotStoreError) as caught:
        await store.save(source_id, response())

    assert caught.value.code == "invalid_source_id"
    assert list(tmp_path.rglob("*")) == []  # noqa: ASYNC240


async def test_existing_hash_path_with_different_content_is_never_overwritten(
    tmp_path: Path,
) -> None:
    body = b"expected"
    digest = hashlib.sha256(body).hexdigest()
    target = (
        tmp_path
        / "2026"
        / "07"
        / "20"
        / "amazon-news"
        / f"20260720T103000000000Z-{digest}.archive.bin.gz"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(gzip.compress(b"different", mtime=0))
    original = target.read_bytes()
    store = SnapshotStore(tmp_path, clock=lambda: NOW)

    with pytest.raises(SnapshotStoreError) as caught:
        await store.save("amazon-news", response(body))

    assert caught.value.code == "hash_path_conflict"
    assert target.read_bytes() == original


async def test_reference_metadata_never_contains_request_query_or_sensitive_headers(
    tmp_path: Path,
) -> None:
    secret = "top-secret-value"
    store = SnapshotStore(tmp_path, clock=lambda: NOW)
    fetched = response(
        url=f"https://news.example.com/items?token={secret}",
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Authorization": f"Bearer {secret}",
            "Set-Cookie": f"session={secret}",
            "X-Api-Key": secret,
        },
    )

    reference = await store.save("amazon-news", fetched)
    rendered_metadata = repr(asdict(reference))

    assert reference.media_type == "text/html"
    assert secret not in rendered_metadata
    assert "token" not in rendered_metadata.lower()
    assert "authorization" not in rendered_metadata.lower()
    assert "cookie" not in rendered_metadata.lower()


async def test_snapshot_created_seven_days_and_one_second_ago_is_deleted(
    tmp_path: Path,
) -> None:
    cutoff = NOW - timedelta(days=7)
    expired_store = SnapshotStore(tmp_path, clock=lambda: cutoff - timedelta(seconds=1))
    expired = await expired_store.save(
        "media-gdelt-cross-border",
        response(b"expired media"),
        temporary=True,
    )

    removed = await SnapshotStore(tmp_path).prune_before(cutoff)

    assert removed == 1
    assert not (tmp_path / expired.relative_path).exists()


async def test_snapshot_created_six_days_twenty_three_hours_fifty_nine_minutes_ago_is_kept(
    tmp_path: Path,
) -> None:
    cutoff = NOW - timedelta(days=7)
    recent_store = SnapshotStore(
        tmp_path,
        clock=lambda: NOW - timedelta(days=6, hours=23, minutes=59),
    )
    recent = await recent_store.save(
        "media-gdelt-cross-border",
        response(b"recent media"),
        temporary=True,
    )

    removed = await SnapshotStore(tmp_path).prune_before(cutoff)

    assert removed == 0
    assert (tmp_path / recent.relative_path).exists()


async def test_global_prune_keeps_expired_archive_snapshots(tmp_path: Path) -> None:
    expired_store = SnapshotStore(tmp_path, clock=lambda: NOW - timedelta(days=8))
    archived = await expired_store.save("amazon-news", response(b"official archive"))

    removed = await SnapshotStore(tmp_path).prune_before(NOW - timedelta(days=7))

    assert removed == 0
    assert (tmp_path / archived.relative_path).exists()


async def test_legacy_snapshot_uses_utc_day_start_as_fail_safe_timestamp(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 7, 13, 10, tzinfo=UTC)
    body = b"legacy temporary media"
    digest = hashlib.sha256(body).hexdigest()
    legacy = tmp_path / "2026" / "07" / "13" / "legacy-media" / f"{digest}.bin.gz"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(gzip.compress(body, mtime=0))

    removed = await SnapshotStore(tmp_path).prune_before(
        cutoff,
        legacy_temporary_source_ids=("legacy-media",),
    )

    assert removed == 1
    assert not legacy.exists()


async def test_legacy_media_metadata_snapshot_is_not_pruned_by_source_name(
    tmp_path: Path,
) -> None:
    body = b"legacy media metadata"
    digest = hashlib.sha256(body).hexdigest()
    legacy = (
        tmp_path
        / "2026"
        / "07"
        / "12"
        / "media-gdelt-metadata"
        / f"{digest}.bin.gz"
    )
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(gzip.compress(body, mtime=0))

    removed = await SnapshotStore(tmp_path).prune_before(NOW - timedelta(days=7))

    assert removed == 0
    assert legacy.exists()


async def test_legacy_full_text_snapshot_is_pruned_by_exact_repository_path(
    tmp_path: Path,
) -> None:
    body = b"legacy full text"
    digest = hashlib.sha256(body).hexdigest()
    relative = f"2026/07/12/media-gdelt-mixed/{digest}.bin.gz"
    legacy = tmp_path / relative
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(gzip.compress(body, mtime=0))

    removed = await SnapshotStore(tmp_path).prune_before(
        NOW - timedelta(days=7),
        legacy_temporary_paths=(relative,),
    )

    assert removed == 1
    assert not legacy.exists()


async def test_prune_rejects_escape_source_id_without_touching_outside_file(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-snapshot.bin.gz"
    outside.write_bytes(b"do not delete")
    store = SnapshotStore(tmp_path, clock=lambda: NOW)

    with pytest.raises(SnapshotStoreError) as caught:
        await store.prune_source_before("../outside", NOW)

    assert caught.value.code == "invalid_source_id"
    assert outside.read_bytes() == b"do not delete"
