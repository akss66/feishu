from __future__ import annotations

import gzip
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
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
    assert reference.relative_path == f"2026/07/20/amazon-news/{digest}.bin.gz"
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
    target = tmp_path / "2026" / "07" / "20" / "amazon-news" / f"{digest}.bin.gz"
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
