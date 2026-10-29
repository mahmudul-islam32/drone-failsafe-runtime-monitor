"""Tests for data models."""

import time

from failsafe_monitor.models import (
    ActionRequest,
    ActionResult,
    Battery,
    ConnectionState,
    FlightMode,
    Position,
    Severity,
    VehicleState,
    Violation,
)


class TestSeverity:
    """Tests for Severity enum ordering."""

    def test_severity_ordering(self) -> None:
        """Verify severity levels are correctly ordered."""
        assert Severity.LOG_ONLY < Severity.WARN
        assert Severity.WARN < Severity.RTL
        assert Severity.RTL < Severity.LAND
        assert Severity.LOG_ONLY < Severity.LAND

    def test_severity_comparison_operators(self) -> None:
        """Test all comparison operators."""
        assert Severity.WARN <= Severity.WARN
        assert Severity.WARN <= Severity.RTL
        assert Severity.LAND >= Severity.RTL
        assert Severity.LAND > Severity.WARN
        assert not (Severity.WARN > Severity.RTL)


class TestPosition:
    """Tests for Position model."""

    def test_position_creation(self) -> None:
        """Test creating a position."""
        pos = Position(
            latitude_deg=47.397742,
            longitude_deg=8.545594,
            absolute_altitude_m=450.0,
            relative_altitude_m=10.0,
        )
        assert pos.latitude_deg == 47.397742
        assert pos.longitude_deg == 8.545594
        assert pos.absolute_altitude_m == 450.0
        assert pos.relative_altitude_m == 10.0
        assert pos.timestamp_s > 0

    def test_position_with_custom_timestamp(self) -> None:
        """Test position with explicit timestamp."""
        ts = 1234567890.0
        pos = Position(
            latitude_deg=0.0,
            longitude_deg=0.0,
            absolute_altitude_m=0.0,
            relative_altitude_m=0.0,
            timestamp_s=ts,
        )
        assert pos.timestamp_s == ts


class TestBattery:
    """Tests for Battery model."""

    def test_battery_creation(self) -> None:
        """Test creating a battery state."""
        bat = Battery(remaining_percent=75.0, voltage_v=12.4)
        assert bat.remaining_percent == 75.0
        assert bat.voltage_v == 12.4

    def test_battery_without_voltage(self) -> None:
        """Test battery state without voltage."""
        bat = Battery(remaining_percent=50.0)
        assert bat.voltage_v is None


class TestVehicleState:
    """Tests for VehicleState model."""

    def test_default_state(self) -> None:
        """Test default vehicle state."""
        state = VehicleState()
        assert state.position is None
        assert state.battery is None
        assert state.health is None
        assert state.flight_mode == FlightMode.UNKNOWN
        assert state.is_armed is False
        assert state.connection_state == ConnectionState.DISCONNECTED

    def test_position_age_with_no_position(self) -> None:
        """Test position age when no position is available."""
        state = VehicleState()
        assert state.position_age_s() is None

    def test_position_age_with_position(self) -> None:
        """Test position age calculation."""
        old_time = time.time() - 2.0
        pos = Position(
            latitude_deg=0.0,
            longitude_deg=0.0,
            absolute_altitude_m=0.0,
            relative_altitude_m=0.0,
            timestamp_s=old_time,
        )
        state = VehicleState(position=pos)
        age = state.position_age_s()
        assert age is not None
        assert 1.9 < age < 2.2  # Allow some tolerance


class TestViolation:
    """Tests for Violation model."""

    def test_violation_creation(self) -> None:
        """Test creating a violation."""
        violation = Violation(
            rule_id="TEST_RULE",
            severity=Severity.RTL,
            message="Test violation",
            values={"key": "value"},
        )
        assert violation.rule_id == "TEST_RULE"
        assert violation.severity == Severity.RTL
        assert violation.message == "Test violation"
        assert violation.values == {"key": "value"}

    def test_debounce_elapsed_no_start(self) -> None:
        """Test debounce elapsed with no start time."""
        violation = Violation(
            rule_id="TEST",
            severity=Severity.WARN,
            message="Test",
        )
        assert violation.debounce_elapsed_s() == 0.0

    def test_debounce_elapsed_with_start(self) -> None:
        """Test debounce elapsed calculation."""
        start = time.time() - 1.5
        violation = Violation(
            rule_id="TEST",
            severity=Severity.WARN,
            message="Test",
            debounce_start_s=start,
        )
        elapsed = violation.debounce_elapsed_s()
        assert 1.4 < elapsed < 1.7


class TestActionRequest:
    """Tests for ActionRequest model."""

    def test_action_request_creation(self) -> None:
        """Test creating an action request."""
        violation = Violation(
            rule_id="GEO",
            severity=Severity.RTL,
            message="Geofence breach",
        )
        request = ActionRequest(
            action=Severity.RTL,
            source_rule_id="GEO",
            source_violation=violation,
        )
        assert request.action == Severity.RTL
        assert request.source_rule_id == "GEO"


class TestActionResult:
    """Tests for ActionResult model."""

    def test_action_result_success(self) -> None:
        """Test successful action result."""
        violation = Violation(
            rule_id="BAT",
            severity=Severity.LAND,
            message="Low battery",
        )
        request = ActionRequest(
            action=Severity.LAND,
            source_rule_id="BAT",
            source_violation=violation,
        )
        result = ActionResult(
            request=request,
            success=True,
            confirmed=True,
            latency_s=0.5,
        )
        assert result.success is True
        assert result.confirmed is True
        assert result.latency_s == 0.5
        assert result.error_message is None

    def test_action_result_failure(self) -> None:
        """Test failed action result."""
        violation = Violation(
            rule_id="BAT",
            severity=Severity.RTL,
            message="Low battery",
        )
        request = ActionRequest(
            action=Severity.RTL,
            source_rule_id="BAT",
            source_violation=violation,
        )
        result = ActionResult(
            request=request,
            success=False,
            retry_count=2,
            error_message="Timeout waiting for mode change",
        )
        assert result.success is False
        assert result.retry_count == 2
        assert result.error_message == "Timeout waiting for mode change"
