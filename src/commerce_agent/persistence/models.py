from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC timestamps while restoring timezone awareness on SQLite."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class GroupBinding(Base):
    __tablename__ = "group_bindings"

    chat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    entry_url: Mapped[str] = mapped_column(Text, nullable=False)
    trust_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    collector: Mapped[str] = mapped_column(String(32), nullable=False)
    compliance: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    regions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    language_hint: Mapped[str | None] = mapped_column(String(32))
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    collector_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    terms_url: Mapped[str] = mapped_column(Text, nullable=False)
    robots_url: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[date] = mapped_column(Date, nullable=False)
    compliance_notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(UTC)
    )


class SourcePlatform(Base):
    __tablename__ = "source_platforms"

    source_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    platform: Mapped[str] = mapped_column(String(32), primary_key=True)

    __table_args__ = (Index("ix_source_platforms_platform", "platform"),)


class FetchRun(Base):
    __tablename__ = "fetch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    http_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_not_modified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_summary: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (Index("ix_fetch_runs_source_started", "source_id", "started_at"),)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    content_group_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "canonical_url", name="uq_documents_source_url"),
        Index("ix_documents_content_group_hash", "content_group_hash"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    language_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    author: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "document_id", "content_hash", name="uq_document_versions_document_hash"
        ),
        Index("ix_document_versions_content_hash", "content_hash"),
    )


class SourceHealth(Base):
    __tablename__ = "source_health"

    source_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    next_scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
