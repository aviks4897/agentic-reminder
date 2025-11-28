from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


@dataclass
class EventRecord:
    """
    Simple structured event payload for observability hooks.
    """

    name: str
    payload: Dict[str, Any]
    timestamp: str


EventListener = Callable[[EventRecord], None]


class EventEmitter:
    """
    Lightweight event emitter that keeps logic unchanged while allowing
    listeners to react to notable moments (e.g., tool calls).
    """

    def __init__(self) -> None:
        self._listeners: List[EventListener] = []

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsubscribe

    def emit(self, name: str, payload: Optional[Dict[str, Any]] = None) -> EventRecord:
        record = EventRecord(
            name=name,
            payload=payload or {},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        for listener in list(self._listeners):
            try:
                listener(record)
            except Exception as exc:  # pragma: no cover - guard rail for observers
                print(f"[EVENT] listener error for {name}: {exc}")
        return record


event_bus = EventEmitter()
emit_event = event_bus.emit
