from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from commerce_agent.ingestion.scheduler import IngestionScheduler
from commerce_agent.runtime import RuntimeResources, _serve


class AsyncCloser:
    def __init__(self, name: str, events: list[str]) -> None:
        self._name = name
        self._events = events

    async def aclose(self) -> None:
        self._events.append(self._name)


class ClientCloser:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def close(self) -> None:
        self._events.append("deepseek")


class DatabaseCloser:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def dispose(self) -> None:
        self._events.append("database")


class ChannelCloser:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def disconnect(self) -> None:
        self._events.append("channel")


class Adapter:
    def __init__(
        self,
        events: list[str],
        connect: Callable[[], object] | None = None,
    ) -> None:
        self._events = events
        self._connect = connect

    async def connect(self) -> None:
        self._events.append("connect")
        if self._connect is not None:
            result = self._connect()
            if asyncio.iscoroutine(result):
                await result

    async def close(self) -> None:
        self._events.append("adapter")


class Scheduler:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def start(self) -> None:
        self._events.append("scheduler_start")

    async def aclose(self) -> None:
        self._events.append("scheduler_stop")


def resources(
    events: list[str],
    *,
    adapter: Adapter | None = None,
    scheduler: object | None = None,
) -> RuntimeResources:
    return RuntimeResources(
        database=DatabaseCloser(events),
        openai_client=ClientCloser(events),
        channel=ChannelCloser(events),
        adapter=adapter or Adapter(events),
        scheduler=scheduler or Scheduler(events),
        ingestion_resources=(
            AsyncCloser("http", events),
            AsyncCloser("browser", events),
        ),
    )


async def test_scheduler_starts_before_blocking_feishu_connection() -> None:
    events: list[str] = []
    connected = asyncio.Event()
    release = asyncio.Event()

    async def block_connect() -> None:
        connected.set()
        await release.wait()

    task = asyncio.create_task(
        _serve(resources(events, adapter=Adapter(events, block_connect)), scheduler_enabled=True)
    )
    await asyncio.wait_for(connected.wait(), timeout=1)
    assert events[:2] == ["scheduler_start", "connect"]

    release.set()
    await task


async def test_ingestion_failure_does_not_stop_feishu_connection() -> None:
    events: list[str] = []

    class FailingService:
        async def run_all(self, trigger: object) -> tuple[object, ...]:
            del trigger
            events.append("ingestion_failed")
            raise RuntimeError("source failed")

    class ImmediateBackend:
        def add_job(self, callback: Callable[[], object], **kwargs: object) -> None:
            del kwargs
            self.callback = callback

        def start(self) -> None:
            self.task = asyncio.create_task(self.callback())

        def shutdown(self, *, wait: bool) -> None:
            assert wait is True

    backend = ImmediateBackend()
    scheduler = IngestionScheduler(FailingService(), scheduler=backend)
    await _serve(resources(events, scheduler=scheduler), scheduler_enabled=True)
    await backend.task

    assert "ingestion_failed" in events
    assert "connect" in events


async def test_feishu_connection_failure_still_stops_scheduler() -> None:
    events: list[str] = []

    def fail_connect() -> None:
        raise RuntimeError("connection failed")

    with pytest.raises(RuntimeError, match="connection failed"):
        await _serve(
            resources(events, adapter=Adapter(events, fail_connect)),
            scheduler_enabled=True,
        )

    assert events[:4] == ["scheduler_start", "connect", "scheduler_stop", "adapter"]


async def test_all_resources_close_once_in_shutdown_order() -> None:
    events: list[str] = []

    await _serve(resources(events), scheduler_enabled=True)

    assert events == [
        "scheduler_start",
        "connect",
        "scheduler_stop",
        "adapter",
        "channel",
        "http",
        "browser",
        "deepseek",
        "database",
    ]


async def test_disabled_scheduler_is_neither_started_nor_stopped() -> None:
    events: list[str] = []

    await _serve(resources(events), scheduler_enabled=False)

    assert "scheduler_start" not in events
    assert "scheduler_stop" not in events
