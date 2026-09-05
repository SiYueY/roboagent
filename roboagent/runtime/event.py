"""Per-Run bounded, non-blocking event delivery."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import AsyncIterator

from roboagent.message import FrozenJsonObject, freeze_json_object


class EventConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EventSubscriptionConfig:
    max_queue_size: int = 256
    replay_limit: int = 256

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (self.max_queue_size, self.replay_limit)):
            raise EventConfigurationError("Event queue and replay limits must be positive.")


@dataclass(frozen=True, slots=True)
class AgentEvent:
    run_id: str
    sequence: int
    type: str
    payload: FrozenJsonObject = field(default_factory=FrozenJsonObject)
    timestamp: float = field(default_factory=time)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.run_id, str)
            or not self.run_id
            or not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
            or not isinstance(self.type, str)
            or not self.type
            or not isinstance(self.timestamp, (int, float))
            or isinstance(self.timestamp, bool)
            or not math.isfinite(self.timestamp)
        ):
            raise ValueError("Invalid AgentEvent identity.")
        object.__setattr__(self, "payload", freeze_json_object(self.payload))


_END = object()


class EventSubscription:
    def __init__(self, emitter: "RunEventEmitter", queue: asyncio.Queue[AgentEvent | object]) -> None:
        self._emitter = emitter
        self._queue = queue
        self._closed = False

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[AgentEvent]:
        try:
            while True:
                item = await self._queue.get()
                if item is _END:
                    return
                assert isinstance(item, AgentEvent)
                yield item
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._emitter._remove(self)


class RunEventEmitter:
    TERMINAL = frozenset({"run.completed", "run.failed", "run.cancelled"})

    def __init__(self, run_id: str, config: EventSubscriptionConfig | None = None) -> None:
        self.run_id = run_id
        self.config = config or EventSubscriptionConfig()
        self._sequence = 0
        self._history: deque[AgentEvent] = deque()
        self._subscriptions: dict[EventSubscription, asyncio.Queue[AgentEvent | object]] = {}
        self._terminal = False
        self.dropped_events = 0

    async def emit(self, event_type: str, **payload: object) -> AgentEvent:
        if self._terminal:
            raise RuntimeError("Cannot emit after the terminal Run event.")
        event = AgentEvent(self.run_id, self._sequence, event_type, freeze_json_object(payload))
        self._sequence += 1
        self._retain(event)
        terminal = event_type in self.TERMINAL
        for queue in tuple(self._subscriptions.values()):
            self._enqueue(queue, event, terminal)
        if terminal:
            self._terminal = True
            for queue in tuple(self._subscriptions.values()):
                self._enqueue_end(queue)
        return event

    def subscribe(self, config: EventSubscriptionConfig | None = None) -> EventSubscription:
        options = config or self.config
        queue: asyncio.Queue[AgentEvent | object] = asyncio.Queue(options.max_queue_size + 1)
        retained = tuple(self._history)[-options.replay_limit :]
        subscription = EventSubscription(self, queue)
        for event in retained:
            self._enqueue(queue, event, event.type in self.TERMINAL)
        if self._terminal:
            self._enqueue_end(queue)
        else:
            self._subscriptions[subscription] = queue
        return subscription

    @property
    def history(self) -> tuple[AgentEvent, ...]:
        return tuple(self._history)

    def _retain(self, event: AgentEvent) -> None:
        if len(self._history) >= self.config.replay_limit:
            index = next((i for i, old in enumerate(self._history) if old.type not in self.TERMINAL), 0)
            del self._history[index]
        self._history.append(event)

    def _enqueue(self, queue: asyncio.Queue[AgentEvent | object], event: AgentEvent, terminal: bool) -> None:
        while queue.qsize() >= queue.maxsize - 1:
            items: list[AgentEvent | object] = []
            while not queue.empty():
                items.append(queue.get_nowait())
            index = next((i for i, item in enumerate(items) if isinstance(item, AgentEvent) and item.type not in self.TERMINAL), 0)
            if items:
                items.pop(index)
                self.dropped_events += 1
            for item in items:
                queue.put_nowait(item)
        queue.put_nowait(event)

    def _enqueue_end(self, queue: asyncio.Queue[AgentEvent | object]) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(_END)

    def _remove(self, subscription: EventSubscription) -> None:
        queue = self._subscriptions.pop(subscription, None)
        if queue is not None:
            while not queue.empty():
                queue.get_nowait()
