from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

from .message import AgentMessage, MessageType


class CollaborationBus:
    """Append-only message log for controlled P2P collaboration."""

    def __init__(self) -> None:
        self._messages: list[AgentMessage] = []
        self._lock = threading.RLock()

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        with self._lock:
            return tuple(self._messages)

    def publish(self, message: AgentMessage) -> AgentMessage:
        with self._lock:
            self._messages.append(message)
        return message

    def request(
        self,
        *,
        room_id: str,
        from_agent: str,
        to_agent: str,
        objective: str,
        required_output: Iterable[str] | None = None,
        context: dict[str, Any] | None = None,
        priority: str = "normal",
    ) -> AgentMessage:
        return self.publish(
            AgentMessage(
                room_id=room_id,
                from_agent=from_agent,
                to_agent=to_agent,
                type=MessageType.REQUEST,
                priority=priority,
                task={
                    "objective": objective,
                    "required_output": list(required_output or []),
                },
                context=context or {},
            )
        )

    def response(
        self,
        *,
        request: AgentMessage,
        from_agent: str,
        result: dict[str, Any],
        status: str = "completed",
        notes: list[str] | None = None,
        confidence: float | None = None,
    ) -> AgentMessage:
        return self.publish(
            AgentMessage(
                room_id=request.room_id,
                from_agent=from_agent,
                to_agent=request.from_agent,
                type=MessageType.RESPONSE,
                priority=request.priority,
                result=result,
                status=status,
                reply_to=request.message_id,
                notes=notes or [],
                confidence=confidence,
            )
        )

    def handoff(
        self,
        *,
        room_id: str,
        from_agent: str,
        to_agent: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentMessage:
        return self.publish(
            AgentMessage(
                room_id=room_id,
                from_agent=from_agent,
                to_agent=to_agent,
                type=MessageType.HANDOFF,
                task={"summary": summary},
                context=payload or {},
                status="completed",
            )
        )

    def update(
        self,
        *,
        room_id: str,
        from_agent: str,
        to_agent: str = "all",
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentMessage:
        return self.publish(
            AgentMessage(
                room_id=room_id,
                from_agent=from_agent,
                to_agent=to_agent,
                type=MessageType.UPDATE,
                task={"summary": summary},
                context=payload or {},
                status="completed",
            )
        )

    def find(
        self,
        *,
        room_id: str | None = None,
        to_agent: str | None = None,
        message_type: MessageType | None = None,
    ) -> list[AgentMessage]:
        with self._lock:
            messages = list(self._messages)
        if room_id is not None:
            messages = [message for message in messages if message.room_id == room_id]
        if to_agent is not None:
            messages = [message for message in messages if message.to_agent == to_agent]
        if message_type is not None:
            messages = [message for message in messages if message.type == message_type]
        return messages

    def to_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [message.to_dict() for message in self._messages]

    @classmethod
    def from_list(cls, items: list[dict[str, Any]]) -> "CollaborationBus":
        bus = cls()
        for item in items:
            bus.publish(AgentMessage.from_dict(item))
        return bus
