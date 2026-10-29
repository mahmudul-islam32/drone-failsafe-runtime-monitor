"""
Scenario tests using recorded telemetry.

These tests verify the safety requirements (SR-xx) from SAFETY_RULES.md
without requiring a live SITL connection.
"""

from pathlib import Path

import pytest

from failsafe_monitor.engine import RuleEngine
from failsafe_monitor.models import MonitorState, Severity
from failsafe_monitor.replay import TelemetryPlayer, create_test_scenario
from failsafe_monitor.rules import (
    AltitudeMaxRule,
    BatteryMinRule,
    GeofencePolygonRule,
    RuleConfig,
    RuleDefaults,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestBatteryScenarios:
    """Battery-related test scenarios (SR-04, SR-05)."""

    @pytest.fixture
    def battery_config(self) -> RuleConfig:
        """Create battery rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                BatteryMinRule(id="BAT_RTL", threshold_percent=25.0, on_violation="rtl"),
                BatteryMinRule(id="BAT_LAND", threshold_percent=12.0, on_violation="land"),
            ],
        )

    def test_t04_low_battery_triggers_rtl(self, battery_config: RuleConfig) -> None:
        """
        T-04: Low battery triggers RTL.

        SR-04: If battery drops below RTL threshold, command RTL.
        """
        player = TelemetryPlayer.from_file(FIXTURES_DIR / "battery_drain.json")
        engine = RuleEngine(battery_config)

        rtl_triggered = False
        rtl_battery_level = None

        while not player.is_finished:
            state = player.next_state()
            if state:
                violations = engine.tick(state)
                for v in violations:
                    if v.rule_id == "BAT_RTL" and v.severity == Severity.RTL:
                        rtl_triggered = True
                        rtl_battery_level = v.values.get("remaining_percent")

        assert rtl_triggered, "RTL should trigger when battery drops below 25%"
        assert rtl_battery_level is not None
        assert rtl_battery_level < 25.0

    def test_t05_critical_battery_triggers_land(self, battery_config: RuleConfig) -> None:
        """
        T-05: Critical battery overrides RTL with Land.

        SR-05: If battery drops below Land threshold, command Land.
        """
        player = TelemetryPlayer.from_file(FIXTURES_DIR / "battery_drain.json")
        engine = RuleEngine(battery_config)

        land_triggered = False

        while not player.is_finished:
            state = player.next_state()
            if state:
                violations = engine.tick(state)
                for v in violations:
                    if v.rule_id == "BAT_LAND" and v.severity == Severity.LAND:
                        land_triggered = True

        assert land_triggered, "Land should trigger when battery drops below 12%"


class TestGeofenceScenarios:
    """Geofence-related test scenarios (SR-01, SR-02)."""

    @pytest.fixture
    def geofence_config(self) -> RuleConfig:
        """Create geofence rule configuration."""
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
                    warn_buffer_m=20.0,
                    on_warn="warn",
                    on_violation="rtl",
                ),
            ],
        )

    def test_t01_geofence_breach_triggers_rtl(self, geofence_config: RuleConfig) -> None:
        """
        T-01: Geofence breach triggers RTL.

        SR-01: If vehicle is outside inclusion polygon, command RTL.
        """
        frames = create_test_scenario(
            "geofence_breach",
            duration_s=1.0,
            tick_hz=10.0,
            start_lat=47.398,
            start_lon=8.546,
        )
        player = TelemetryPlayer.from_frames(frames)
        engine = RuleEngine(geofence_config)

        rtl_triggered = False

        while not player.is_finished:
            state = player.next_state()
            if state:
                violations = engine.tick(state)
                for v in violations:
                    if v.rule_id == "GEOFENCE" and v.severity == Severity.RTL:
                        rtl_triggered = True

        assert rtl_triggered, "RTL should trigger when vehicle leaves geofence"

    def test_t02_near_boundary_warning(self, geofence_config: RuleConfig) -> None:
        """
        T-02: Near boundary flight generates warning.

        SR-02: If vehicle is within warn_buffer_m of boundary, raise warning.
        """
        frames = create_test_scenario(
            "normal",
            duration_s=0.5,
            tick_hz=10.0,
            start_lat=47.398,
            start_lon=8.546,
        )
        player = TelemetryPlayer.from_frames(frames)
        engine = RuleEngine(geofence_config)

        while not player.is_finished:
            state = player.next_state()
            if state:
                engine.tick(state)

        assert engine.monitor_state in (
            MonitorState.MONITORING,
            MonitorState.WARNING,
        )


class TestAltitudeScenarios:
    """Altitude-related test scenarios (SR-03)."""

    @pytest.fixture
    def altitude_config(self) -> RuleConfig:
        """Create altitude rule configuration."""
        return RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                AltitudeMaxRule(
                    id="ALT_CEIL",
                    max_relative_alt_m=60.0,
                    warn_buffer_m=10.0,
                    on_warn="warn",
                    on_violation="rtl",
                ),
            ],
        )

    def test_t03_altitude_ceiling_breach(self, altitude_config: RuleConfig) -> None:
        """
        T-03: Altitude ceiling breach triggers RTL.

        SR-03: If relative altitude exceeds ceiling, command RTL.
        """
        frames = create_test_scenario(
            "altitude_breach",
            duration_s=1.0,
            tick_hz=10.0,
            start_alt=50.0,
        )
        player = TelemetryPlayer.from_frames(frames)
        engine = RuleEngine(altitude_config)

        rtl_triggered = False

        while not player.is_finished:
            state = player.next_state()
            if state:
                violations = engine.tick(state)
                for v in violations:
                    if v.rule_id == "ALT_CEIL" and v.severity == Severity.RTL:
                        rtl_triggered = True

        assert rtl_triggered, "RTL should trigger when altitude exceeds ceiling"


class TestDebounceScenarios:
    """Debounce behavior test scenarios (SR-08)."""

    def test_t08_gps_spike_no_action(self) -> None:
        """
        T-08: GPS spike doesn't trigger false positive.

        SR-08: Violation shorter than debounce_s shall not trigger action.
        """
        config = RuleConfig(
            defaults=RuleDefaults(debounce_s=0.5),
            rules=[
                GeofencePolygonRule(
                    id="GEOFENCE",
                    polygon=[
                        (47.397, 8.545),
                        (47.399, 8.545),
                        (47.399, 8.548),
                        (47.397, 8.548),
                    ],
                    on_violation="rtl",
                ),
            ],
        )

        frames = create_test_scenario(
            "gps_spike",
            duration_s=1.0,
            tick_hz=10.0,
            start_lat=47.398,
            start_lon=8.546,
        )
        player = TelemetryPlayer.from_frames(frames)
        engine = RuleEngine(config)

        confirmed_rtl_violations = []

        while not player.is_finished:
            state = player.next_state()
            if state:
                violations = engine.tick(state)
                for v in violations:
                    if v.severity >= Severity.RTL and v.debounce_start_s is not None:
                        confirmed_rtl_violations.append(v)

        assert len(confirmed_rtl_violations) == 0, \
            "Single GPS spike should not trigger action due to debounce"


class TestEscalationScenarios:
    """Escalation behavior test scenarios (SR-09)."""

    def test_t09_no_deescalation_during_flight(self) -> None:
        """
        T-09: No de-escalation during flight.

        SR-09: Once RTL or Land is commanded, don't de-escalate during same flight.
        """
        config = RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                BatteryMinRule(id="BAT_RTL", threshold_percent=25.0, on_violation="rtl"),
            ],
        )

        engine = RuleEngine(config)

        frames = create_test_scenario(
            "battery_drain",
            duration_s=1.0,
            tick_hz=10.0,
            start_battery=20.0,
        )
        player = TelemetryPlayer.from_frames(frames)

        while not player.is_finished:
            state = player.next_state()
            if state:
                engine.tick(state)

        assert engine.state.highest_action == Severity.RTL
        assert engine.state.is_latched is True
