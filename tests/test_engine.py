"""Tests for the rule engine."""

import time

import pytest

from failsafe_monitor.engine import SEVERITY_MAP, RuleEngine, RuleState
from failsafe_monitor.models import (
    Battery,
    ConnectionState,
    FlightMode,
    MonitorState,
    Position,
    Severity,
    VehicleState,
)
from failsafe_monitor.rules import (
    AltitudeMaxRule,
    AltitudeMinRule,
    BatteryMinRule,
    ConnectionLostRule,
    GeofencePolygonRule,
    RuleConfig,
    RuleDefaults,
    TelemetryMaxAgeRule,
)


class TestRuleState:
    """Tests for RuleState."""

    def test_initial_state(self) -> None:
        """Test initial rule state."""
        state = RuleState(rule_id="TEST")
        assert state.rule_id == "TEST"
        assert state.is_violated is False
        assert state.is_warning is False
        assert state.violation_start_s is None
        assert state.debounce_met is False


class TestSeverityMap:
    """Tests for severity mapping."""

    def test_all_severities_mapped(self) -> None:
        """Test that all severity strings are mapped."""
        assert SEVERITY_MAP["log_only"] == Severity.LOG_ONLY
        assert SEVERITY_MAP["warn"] == Severity.WARN
        assert SEVERITY_MAP["rtl"] == Severity.RTL
        assert SEVERITY_MAP["land"] == Severity.LAND


class TestRuleEngine:
    """Tests for RuleEngine."""

    @pytest.fixture
    def basic_config(self) -> RuleConfig:
        """Create a basic rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                BatteryMinRule(id="BAT_LOW", threshold_percent=20.0, on_violation="rtl"),
            ],
        )

    @pytest.fixture
    def armed_state(self) -> VehicleState:
        """Create an armed vehicle state."""
        return VehicleState(
            position=Position(
                latitude_deg=47.398,
                longitude_deg=8.546,
                absolute_altitude_m=450.0,
                relative_altitude_m=10.0,
            ),
            battery=Battery(remaining_percent=80.0, voltage_v=12.4),
            flight_mode=FlightMode.MISSION,
            is_armed=True,
            connection_state=ConnectionState.CONNECTED,
        )

    def test_engine_initialization(self, basic_config: RuleConfig) -> None:
        """Test engine initialization."""
        engine = RuleEngine(basic_config)
        assert engine.state.monitor_state == MonitorState.CONNECTING
        assert engine.state.highest_action == Severity.LOG_ONLY

    def test_engine_reset(self, basic_config: RuleConfig) -> None:
        """Test engine reset."""
        engine = RuleEngine(basic_config)
        engine._state.monitor_state = MonitorState.RTL
        engine._state.highest_action = Severity.RTL

        engine.reset()

        assert engine.state.monitor_state == MonitorState.CONNECTING
        assert engine.state.highest_action == Severity.LOG_ONLY

    def test_monitor_state_transitions_to_monitoring(
        self, basic_config: RuleConfig, armed_state: VehicleState
    ) -> None:
        """Test transition from CONNECTING to MONITORING."""
        engine = RuleEngine(basic_config)
        assert engine.monitor_state == MonitorState.CONNECTING

        engine.tick(armed_state)

        assert engine.monitor_state == MonitorState.MONITORING


class TestBatteryRule:
    """Tests for battery rule evaluation."""

    @pytest.fixture
    def battery_config(self) -> RuleConfig:
        """Create a battery rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                BatteryMinRule(id="BAT_RTL", threshold_percent=25.0, on_violation="rtl"),
                BatteryMinRule(id="BAT_LAND", threshold_percent=10.0, on_violation="land"),
            ],
        )

    def test_no_violation_above_threshold(self, battery_config: RuleConfig) -> None:
        """Test no violation when battery is above threshold."""
        engine = RuleEngine(battery_config)
        state = VehicleState(
            battery=Battery(remaining_percent=50.0),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        assert len(violations) == 0

    def test_violation_below_threshold(self, battery_config: RuleConfig) -> None:
        """Test violation when battery is below threshold."""
        engine = RuleEngine(battery_config)
        state = VehicleState(
            battery=Battery(remaining_percent=20.0),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "BAT_RTL"
        assert violations[0].severity == Severity.RTL

    def test_land_overrides_rtl(self, battery_config: RuleConfig) -> None:
        """Test land severity overrides RTL."""
        engine = RuleEngine(battery_config)
        state = VehicleState(
            battery=Battery(remaining_percent=5.0),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        severities = [v.severity for v in violations]
        assert Severity.LAND in severities


class TestAltitudeRule:
    """Tests for altitude rule evaluation."""

    @pytest.fixture
    def altitude_config(self) -> RuleConfig:
        """Create an altitude rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                AltitudeMaxRule(
                    id="ALT_CEIL",
                    max_relative_alt_m=60.0,
                    warn_buffer_m=5.0,
                    on_warn="warn",
                    on_violation="rtl",
                ),
                AltitudeMinRule(
                    id="ALT_FLOOR",
                    min_relative_alt_m=2.0,
                    on_violation="warn",
                ),
            ],
        )

    def test_no_violation_within_limits(self, altitude_config: RuleConfig) -> None:
        """Test no violation when altitude is within limits."""
        engine = RuleEngine(altitude_config)
        state = VehicleState(
            position=Position(
                latitude_deg=0,
                longitude_deg=0,
                absolute_altitude_m=0,
                relative_altitude_m=30.0,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        assert len(violations) == 0

    def test_violation_above_ceiling(self, altitude_config: RuleConfig) -> None:
        """Test violation when above altitude ceiling."""
        engine = RuleEngine(altitude_config)
        state = VehicleState(
            position=Position(
                latitude_deg=0,
                longitude_deg=0,
                absolute_altitude_m=0,
                relative_altitude_m=70.0,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "ALT_CEIL"
        assert violations[0].severity == Severity.RTL

    def test_warning_near_ceiling(self, altitude_config: RuleConfig) -> None:
        """Test warning when near altitude ceiling."""
        engine = RuleEngine(altitude_config)
        state = VehicleState(
            position=Position(
                latitude_deg=0,
                longitude_deg=0,
                absolute_altitude_m=0,
                relative_altitude_m=57.0,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "ALT_CEIL"
        assert violations[0].severity == Severity.WARN

    def test_violation_below_floor(self, altitude_config: RuleConfig) -> None:
        """Test violation when below altitude floor."""
        engine = RuleEngine(altitude_config)
        state = VehicleState(
            position=Position(
                latitude_deg=0,
                longitude_deg=0,
                absolute_altitude_m=0,
                relative_altitude_m=1.0,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "ALT_FLOOR"


class TestGeofenceRule:
    """Tests for geofence rule evaluation."""

    @pytest.fixture
    def geofence_config(self) -> RuleConfig:
        """Create a geofence rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                GeofencePolygonRule(
                    id="GEOFENCE",
                    polygon=[
                        (47.397, 8.545),
                        (47.399, 8.545),
                        (47.399, 8.548),
                        (47.397, 8.548),
                    ],
                    warn_buffer_m=50.0,
                    on_warn="warn",
                    on_violation="rtl",
                ),
            ],
        )

    def test_no_violation_inside_polygon(self, geofence_config: RuleConfig) -> None:
        """Test no violation when inside the polygon."""
        engine = RuleEngine(geofence_config)
        state = VehicleState(
            position=Position(
                latitude_deg=47.398,
                longitude_deg=8.5465,
                absolute_altitude_m=450,
                relative_altitude_m=10,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        assert len(violations) == 0

    def test_violation_outside_polygon(self, geofence_config: RuleConfig) -> None:
        """Test violation when outside the polygon."""
        engine = RuleEngine(geofence_config)
        state = VehicleState(
            position=Position(
                latitude_deg=47.400,
                longitude_deg=8.550,
                absolute_altitude_m=450,
                relative_altitude_m=10,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "GEOFENCE"
        assert violations[0].severity == Severity.RTL


class TestTelemetryAgeRule:
    """Tests for telemetry staleness rule."""

    @pytest.fixture
    def telemetry_config(self) -> RuleConfig:
        """Create a telemetry age rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                TelemetryMaxAgeRule(
                    id="STALE_POS",
                    stream="position",
                    max_age_s=2.0,
                    on_violation="rtl",
                ),
            ],
        )

    def test_no_violation_fresh_telemetry(self, telemetry_config: RuleConfig) -> None:
        """Test no violation with fresh telemetry."""
        engine = RuleEngine(telemetry_config)
        state = VehicleState(
            position=Position(
                latitude_deg=0,
                longitude_deg=0,
                absolute_altitude_m=0,
                relative_altitude_m=10,
                timestamp_s=time.time(),
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        assert len(violations) == 0

    def test_violation_stale_telemetry(self, telemetry_config: RuleConfig) -> None:
        """Test violation with stale telemetry."""
        engine = RuleEngine(telemetry_config)
        old_time = time.time() - 5.0
        state = VehicleState(
            position=Position(
                latitude_deg=0,
                longitude_deg=0,
                absolute_altitude_m=0,
                relative_altitude_m=10,
                timestamp_s=old_time,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "STALE_POS"
        assert "stale" in violations[0].message.lower()

    def test_violation_no_telemetry(self, telemetry_config: RuleConfig) -> None:
        """Test violation when telemetry is missing."""
        engine = RuleEngine(telemetry_config)
        state = VehicleState(
            position=None,
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "STALE_POS"


class TestConnectionRule:
    """Tests for connection loss rule."""

    @pytest.fixture
    def connection_config(self) -> RuleConfig:
        """Create a connection loss rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                ConnectionLostRule(id="LINK_LOST", on_violation="log_only"),
            ],
        )

    def test_no_violation_when_connected(self, connection_config: RuleConfig) -> None:
        """Test no violation when connected."""
        engine = RuleEngine(connection_config)
        state = VehicleState(connection_state=ConnectionState.CONNECTED)

        violations = engine.tick(state)
        assert len(violations) == 0

    def test_violation_when_disconnected(self, connection_config: RuleConfig) -> None:
        """Test violation when disconnected."""
        engine = RuleEngine(connection_config)
        engine._state.monitor_state = MonitorState.MONITORING
        state = VehicleState(connection_state=ConnectionState.DISCONNECTED)

        violations = engine.tick(state)

        assert len(violations) == 1
        assert violations[0].rule_id == "LINK_LOST"
        assert engine.monitor_state == MonitorState.BLIND


class TestDebouncing:
    """Tests for debounce behavior."""

    @pytest.fixture
    def debounce_config(self) -> RuleConfig:
        """Create a configuration with debouncing."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=1.0),
            rules=[
                BatteryMinRule(id="BAT", threshold_percent=20.0, on_violation="rtl"),
            ],
        )

    def test_no_immediate_violation(self, debounce_config: RuleConfig) -> None:
        """Test that violations don't trigger immediately with debounce."""
        engine = RuleEngine(debounce_config)
        state = VehicleState(
            battery=Battery(remaining_percent=15.0),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        assert len(violations) == 0

    def test_violation_after_debounce(self, debounce_config: RuleConfig) -> None:
        """Test violation triggers after debounce period."""
        engine = RuleEngine(debounce_config)
        state = VehicleState(
            battery=Battery(remaining_percent=15.0),
            connection_state=ConnectionState.CONNECTED,
        )

        engine.tick(state)

        rule_state = engine._state.rule_states["BAT"]
        rule_state.violation_start_s = time.time() - 2.0

        violations = engine.tick(state)
        assert len(violations) == 1


class TestEscalation:
    """Tests for severity escalation."""

    @pytest.fixture
    def escalation_config(self) -> RuleConfig:
        """Create a configuration for escalation testing."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                BatteryMinRule(id="BAT_RTL", threshold_percent=25.0, on_violation="rtl"),
                BatteryMinRule(id="BAT_LAND", threshold_percent=10.0, on_violation="land"),
            ],
        )

    def test_no_deescalation_during_flight(self, escalation_config: RuleConfig) -> None:
        """Test that severity doesn't decrease during a flight."""
        engine = RuleEngine(escalation_config)

        low_battery = VehicleState(
            battery=Battery(remaining_percent=5.0),
            connection_state=ConnectionState.CONNECTED,
            is_armed=True,
        )
        engine.tick(low_battery)

        assert engine.state.highest_action == Severity.LAND

        normal_battery = VehicleState(
            battery=Battery(remaining_percent=50.0),
            connection_state=ConnectionState.CONNECTED,
            is_armed=True,
        )
        engine.tick(normal_battery)

        assert engine.state.highest_action == Severity.LAND
        assert engine.state.is_latched is True

    def test_latch_released_on_disarm(self, escalation_config: RuleConfig) -> None:
        """Test that latch is released when vehicle disarms."""
        engine = RuleEngine(escalation_config)

        low_battery = VehicleState(
            battery=Battery(remaining_percent=5.0),
            connection_state=ConnectionState.CONNECTED,
            is_armed=True,
        )
        engine.tick(low_battery)
        assert engine.state.highest_action == Severity.LAND

        disarmed = VehicleState(
            battery=Battery(remaining_percent=50.0),
            connection_state=ConnectionState.CONNECTED,
            is_armed=False,
        )
        engine.tick(disarmed)

        assert engine.state.highest_action == Severity.LOG_ONLY
        assert engine.state.is_latched is False


class TestActionRequest:
    """Tests for action request generation."""

    @pytest.fixture
    def action_config(self) -> RuleConfig:
        """Create a configuration for action testing."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                BatteryMinRule(id="BAT", threshold_percent=20.0, on_violation="rtl"),
            ],
        )

    def test_action_request_for_rtl(self, action_config: RuleConfig) -> None:
        """Test action request is generated for RTL severity."""
        engine = RuleEngine(action_config)
        state = VehicleState(
            battery=Battery(remaining_percent=15.0),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        request = engine.get_action_request(violations)

        assert request is not None
        assert request.action == Severity.RTL
        assert request.source_rule_id == "BAT"

    def test_no_action_request_for_warn(self) -> None:
        """Test no action request for warning severity."""
        config = RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                AltitudeMaxRule(
                    id="ALT", max_relative_alt_m=60.0, warn_buffer_m=10.0, on_warn="warn"
                ),
            ],
        )
        engine = RuleEngine(config)
        state = VehicleState(
            position=Position(
                latitude_deg=0,
                longitude_deg=0,
                absolute_altitude_m=0,
                relative_altitude_m=55.0,
            ),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        request = engine.get_action_request(violations)

        assert request is None

    def test_no_action_request_without_violations(self, action_config: RuleConfig) -> None:
        """Test no action request when there are no violations."""
        engine = RuleEngine(action_config)
        state = VehicleState(
            battery=Battery(remaining_percent=80.0),
            connection_state=ConnectionState.CONNECTED,
        )

        violations = engine.tick(state)
        request = engine.get_action_request(violations)

        assert request is None


class TestRecordExecutedAction:
    """The executor's actual action is reflected in the engine state."""

    def test_escalated_land_sets_land_state(self) -> None:
        engine = RuleEngine(RuleConfig(rules=[]))
        engine.tick(VehicleState(is_armed=True, connection_state=ConnectionState.CONNECTED))
        engine.record_executed_action(Severity.LAND)
        assert engine.monitor_state == MonitorState.LAND
        assert engine.state.highest_action == Severity.LAND
        assert engine.state.is_latched

    def test_warn_is_ignored(self) -> None:
        engine = RuleEngine(RuleConfig(rules=[]))
        engine.record_executed_action(Severity.WARN)
        assert engine.monitor_state == MonitorState.CONNECTING
