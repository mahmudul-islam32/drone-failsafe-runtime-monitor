"""
Event logging for the failsafe monitor.

Logs events in JSONL format for after-flight analysis.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Types of events logged by the monitor."""

    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    TELEMETRY = "telemetry"
    VIOLATION = "violation"
    VIOLATION_CLEARED = "violation_cleared"
    ACTION_REQUESTED = "action_requested"
    ACTION_EXECUTED = "action_executed"
    ACTION_CONFIRMED = "action_confirmed"
    ACTION_FAILED = "action_failed"
    STATE_CHANGE = "state_change"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Event:
    """A single logged event."""

    event_type: EventType
    timestamp_s: float = field(default_factory=time.time)
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    rule_id: str | None = None
    severity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary for JSON serialization."""
        result = {
            "event_type": self.event_type.value,
            "timestamp_s": self.timestamp_s,
            "timestamp_iso": datetime.fromtimestamp(
                self.timestamp_s, tz=timezone.utc
            ).isoformat(),
            "message": self.message,
        }
        if self.data:
            result["data"] = self.data
        if self.rule_id:
            result["rule_id"] = self.rule_id
        if self.severity:
            result["severity"] = self.severity
        return result

    def to_json(self) -> str:
        """Convert to a JSON string."""
        return json.dumps(self.to_dict(), default=str)


class EventLogger:
    """
    Logs events to a JSONL file.

    Each line is a JSON object representing one event.
    """

    def __init__(
        self,
        log_dir: Path | str = "logs",
        session_id: str | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        if session_id is None:
            session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        self.session_id = session_id
        self.log_file = self.log_dir / f"events_{session_id}.jsonl"
        self._events: list[Event] = []
        self._file_handle: Any = None

        logger.info("Event log: %s", self.log_file)

    def open(self) -> None:
        """Open the log file for writing."""
        self._file_handle = open(self.log_file, "a", encoding="utf-8")  # noqa: SIM115

    def close(self) -> None:
        """Close the log file."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    def __enter__(self) -> EventLogger:
        self.open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def log(self, event: Event) -> None:
        """Log an event."""
        self._events.append(event)

        if self._file_handle:
            self._file_handle.write(event.to_json() + "\n")
            self._file_handle.flush()

        log_level = logging.INFO
        if event.event_type in (EventType.ERROR, EventType.ACTION_FAILED):
            log_level = logging.ERROR
        elif event.event_type in (EventType.WARNING, EventType.VIOLATION):
            log_level = logging.WARNING

        logger.log(log_level, "[%s] %s", event.event_type.value, event.message)

    def log_startup(self, config_hash: str, rules_file: str, vehicle_id: str) -> None:
        """Log monitor startup."""
        self.log(Event(
            event_type=EventType.STARTUP,
            message="Monitor started",
            data={
                "config_hash": config_hash,
                "rules_file": rules_file,
                "vehicle_id": vehicle_id,
            },
        ))

    def log_shutdown(self, reason: str = "normal") -> None:
        """Log monitor shutdown."""
        self.log(Event(
            event_type=EventType.SHUTDOWN,
            message=f"Monitor stopped: {reason}",
            data={"reason": reason},
        ))

    def log_connected(self, uuid: str | None = None) -> None:
        """Log connection established."""
        self.log(Event(
            event_type=EventType.CONNECTED,
            message="Connected to vehicle",
            data={"uuid": uuid} if uuid else {},
        ))

    def log_disconnected(self, reason: str = "unknown") -> None:
        """Log connection lost."""
        self.log(Event(
            event_type=EventType.DISCONNECTED,
            message=f"Connection lost: {reason}",
            data={"reason": reason},
        ))

    def log_violation(
        self,
        rule_id: str,
        severity: str,
        message: str,
        values: dict[str, Any],
    ) -> None:
        """Log a rule violation."""
        self.log(Event(
            event_type=EventType.VIOLATION,
            message=message,
            data=values,
            rule_id=rule_id,
            severity=severity,
        ))

    def log_violation_cleared(self, rule_id: str) -> None:
        """Log a violation being cleared."""
        self.log(Event(
            event_type=EventType.VIOLATION_CLEARED,
            message=f"Violation cleared: {rule_id}",
            rule_id=rule_id,
        ))

    def log_action_requested(
        self,
        action: str,
        rule_id: str,
        severity: str,
    ) -> None:
        """Log an action request."""
        self.log(Event(
            event_type=EventType.ACTION_REQUESTED,
            message=f"Action requested: {action}",
            data={"action": action},
            rule_id=rule_id,
            severity=severity,
        ))

    def log_action_executed(
        self,
        action: str,
        success: bool,
        latency_s: float,
        retry_count: int = 0,
    ) -> None:
        """Log an action execution result."""
        event_type = EventType.ACTION_CONFIRMED if success else EventType.ACTION_FAILED
        self.log(Event(
            event_type=event_type,
            message=f"Action {'confirmed' if success else 'failed'}: {action}",
            data={
                "action": action,
                "success": success,
                "latency_s": latency_s,
                "retry_count": retry_count,
            },
        ))

    def log_state_change(self, old_state: str, new_state: str) -> None:
        """Log a monitor state change."""
        self.log(Event(
            event_type=EventType.STATE_CHANGE,
            message=f"State: {old_state} -> {new_state}",
            data={"old_state": old_state, "new_state": new_state},
        ))

    def log_warning(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Log a warning."""
        self.log(Event(
            event_type=EventType.WARNING,
            message=message,
            data=data or {},
        ))

    def log_error(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Log an error."""
        self.log(Event(
            event_type=EventType.ERROR,
            message=message,
            data=data or {},
        ))

    @property
    def events(self) -> list[Event]:
        """Return all logged events."""
        return self._events.copy()

    def get_summary(self) -> dict[str, Any]:
        """Generate a summary of the logged events."""
        if not self._events:
            return {"total_events": 0}

        violations = [e for e in self._events if e.event_type == EventType.VIOLATION]
        actions = [e for e in self._events if e.event_type in (
            EventType.ACTION_CONFIRMED, EventType.ACTION_FAILED
        )]
        successful_actions = [e for e in actions if e.data.get("success", False)]

        return {
            "session_id": self.session_id,
            "total_events": len(self._events),
            "start_time": datetime.fromtimestamp(
                self._events[0].timestamp_s, tz=timezone.utc
            ).isoformat(),
            "end_time": datetime.fromtimestamp(
                self._events[-1].timestamp_s, tz=timezone.utc
            ).isoformat(),
            "duration_s": self._events[-1].timestamp_s - self._events[0].timestamp_s,
            "violations": len(violations),
            "actions_attempted": len(actions),
            "actions_successful": len(successful_actions),
            "unique_rules_violated": len({e.rule_id for e in violations if e.rule_id}),
        }


def load_events(log_file: Path | str) -> list[Event]:
    """
    Load events from a JSONL log file.

    Args:
        log_file: Path to the JSONL file

    Returns:
        List of Event objects
    """
    events = []
    log_file = Path(log_file)

    if not log_file.exists():
        return events

    with open(log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                event = Event(
                    event_type=EventType(data["event_type"]),
                    timestamp_s=data["timestamp_s"],
                    message=data.get("message", ""),
                    data=data.get("data", {}),
                    rule_id=data.get("rule_id"),
                    severity=data.get("severity"),
                )
                events.append(event)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning("Failed to parse log line: %s", e)

    return events
