"""Tests for event logging."""

import json
import tempfile
from pathlib import Path

import pytest

from failsafe_monitor.logging import (
    Event,
    EventLogger,
    EventType,
    load_events,
)


class TestEvent:
    """Tests for Event class."""

    def test_event_creation(self) -> None:
        """Test creating an event."""
        event = Event(
            event_type=EventType.VIOLATION,
            message="Test violation",
            data={"key": "value"},
            rule_id="TEST_RULE",
            severity="rtl",
        )
        assert event.event_type == EventType.VIOLATION
        assert event.message == "Test violation"
        assert event.rule_id == "TEST_RULE"
        assert event.severity == "rtl"
        assert event.timestamp_s > 0

    def test_event_to_dict(self) -> None:
        """Test converting event to dictionary."""
        event = Event(
            event_type=EventType.STARTUP,
            message="Test",
            data={"config": "test"},
        )
        d = event.to_dict()

        assert d["event_type"] == "startup"
        assert d["message"] == "Test"
        assert "timestamp_s" in d
        assert "timestamp_iso" in d
        assert d["data"] == {"config": "test"}

    def test_event_to_dict_minimal(self) -> None:
        """Test minimal event to dictionary."""
        event = Event(event_type=EventType.TELEMETRY, message="")
        d = event.to_dict()

        assert "data" not in d
        assert "rule_id" not in d
        assert "severity" not in d

    def test_event_to_json(self) -> None:
        """Test converting event to JSON."""
        event = Event(
            event_type=EventType.CONNECTED,
            message="Connected",
        )
        json_str = event.to_json()

        parsed = json.loads(json_str)
        assert parsed["event_type"] == "connected"
        assert parsed["message"] == "Connected"


class TestEventLogger:
    """Tests for EventLogger class."""

    @pytest.fixture
    def temp_log_dir(self) -> Path:
        """Create a temporary log directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_logger_initialization(self, temp_log_dir: Path) -> None:
        """Test logger initialization."""
        logger = EventLogger(log_dir=temp_log_dir, session_id="test_session")
        assert logger.session_id == "test_session"
        assert logger.log_file == temp_log_dir / "events_test_session.jsonl"

    def test_logger_context_manager(self, temp_log_dir: Path) -> None:
        """Test logger as context manager."""
        with EventLogger(log_dir=temp_log_dir) as logger:
            logger.log(Event(event_type=EventType.STARTUP, message="Test"))

        assert logger.log_file.exists()

    def test_log_writes_to_file(self, temp_log_dir: Path) -> None:
        """Test that events are written to file."""
        with EventLogger(log_dir=temp_log_dir) as logger:
            logger.log(Event(event_type=EventType.STARTUP, message="Start"))
            logger.log(Event(event_type=EventType.SHUTDOWN, message="Stop"))

        content = logger.log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2

        first_event = json.loads(lines[0])
        assert first_event["event_type"] == "startup"

    def test_log_startup(self, temp_log_dir: Path) -> None:
        """Test log_startup helper."""
        with EventLogger(log_dir=temp_log_dir) as logger:
            logger.log_startup(
                config_hash="abc123",
                rules_file="rules.yaml",
                vehicle_id="test-drone",
            )

        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.event_type == EventType.STARTUP
        assert event.data["config_hash"] == "abc123"

    def test_log_violation(self, temp_log_dir: Path) -> None:
        """Test log_violation helper."""
        with EventLogger(log_dir=temp_log_dir) as logger:
            logger.log_violation(
                rule_id="GEO_FENCE",
                severity="rtl",
                message="Outside geofence",
                values={"lat": 47.0, "lon": 8.0},
            )

        event = logger.events[0]
        assert event.event_type == EventType.VIOLATION
        assert event.rule_id == "GEO_FENCE"
        assert event.severity == "rtl"

    def test_log_action_executed_success(self, temp_log_dir: Path) -> None:
        """Test log_action_executed for success."""
        with EventLogger(log_dir=temp_log_dir) as logger:
            logger.log_action_executed(
                action="rtl",
                success=True,
                latency_s=0.5,
            )

        event = logger.events[0]
        assert event.event_type == EventType.ACTION_CONFIRMED
        assert event.data["success"] is True

    def test_log_action_executed_failure(self, temp_log_dir: Path) -> None:
        """Test log_action_executed for failure."""
        with EventLogger(log_dir=temp_log_dir) as logger:
            logger.log_action_executed(
                action="land",
                success=False,
                latency_s=2.0,
                retry_count=1,
            )

        event = logger.events[0]
        assert event.event_type == EventType.ACTION_FAILED
        assert event.data["retry_count"] == 1

    def test_get_summary_empty(self, temp_log_dir: Path) -> None:
        """Test summary with no events."""
        logger = EventLogger(log_dir=temp_log_dir)
        summary = logger.get_summary()
        assert summary["total_events"] == 0

    def test_get_summary_with_events(self, temp_log_dir: Path) -> None:
        """Test summary with events."""
        with EventLogger(log_dir=temp_log_dir) as logger:
            logger.log_startup("hash", "rules.yaml", "drone1")
            logger.log_violation("RULE1", "rtl", "Test", {})
            logger.log_violation("RULE2", "warn", "Test2", {})
            logger.log_action_executed("rtl", True, 0.5)
            logger.log_shutdown()

        summary = logger.get_summary()
        assert summary["total_events"] == 5
        assert summary["violations"] == 2
        assert summary["actions_successful"] == 1
        assert summary["unique_rules_violated"] == 2


class TestLoadEvents:
    """Tests for load_events function."""

    def test_load_events(self) -> None:
        """Test loading events from file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            event_data = [
                {"event_type": "startup", "timestamp_s": 1000, "message": "Start"},
                {"event_type": "violation", "timestamp_s": 1001, "message": "Violation",
                 "rule_id": "TEST", "severity": "rtl"},
                {"event_type": "shutdown", "timestamp_s": 1002, "message": "Stop"},
            ]
            for event in event_data:
                f.write(json.dumps(event) + "\n")
            temp_log_file = Path(f.name)

        events = load_events(temp_log_file)
        temp_log_file.unlink()

        assert len(events) == 3
        assert events[0].event_type == EventType.STARTUP
        assert events[1].rule_id == "TEST"
        assert events[2].event_type == EventType.SHUTDOWN

    def test_load_events_nonexistent_file(self) -> None:
        """Test loading from nonexistent file."""
        events = load_events(Path("/nonexistent/file.jsonl"))
        assert events == []

    def test_load_events_with_invalid_lines(self) -> None:
        """Test loading file with some invalid lines."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            f.write('{"event_type": "startup", "timestamp_s": 1000, "message": "OK"}\n')
            f.write("invalid json line\n")
            f.write('{"event_type": "shutdown", "timestamp_s": 1001, "message": "OK"}\n')
            f.write("\n")
            temp_path = Path(f.name)

        events = load_events(temp_path)
        assert len(events) == 2
        temp_path.unlink()
