"""Tests for rule schema validation."""

import pytest
from pydantic import ValidationError

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


class TestRuleDefaults:
    """Tests for RuleDefaults."""

    def test_defaults(self) -> None:
        """Test default values."""
        defaults = RuleDefaults()
        assert defaults.debounce_s == 1.0
        assert defaults.hysteresis == 0.05

    def test_custom_defaults(self) -> None:
        """Test custom values."""
        defaults = RuleDefaults(debounce_s=2.0, hysteresis=0.1)
        assert defaults.debounce_s == 2.0
        assert defaults.hysteresis == 0.1

    def test_invalid_negative_debounce(self) -> None:
        """Test negative debounce is rejected."""
        with pytest.raises(ValidationError):
            RuleDefaults(debounce_s=-1.0)

    def test_invalid_hysteresis_range(self) -> None:
        """Test hysteresis must be in [0, 1]."""
        with pytest.raises(ValidationError):
            RuleDefaults(hysteresis=1.5)


class TestGeofencePolygonRule:
    """Tests for GeofencePolygonRule."""

    def test_valid_polygon(self) -> None:
        """Test valid polygon rule."""
        rule = GeofencePolygonRule(
            id="TEST_GEO",
            polygon=[
                (47.0, 8.0),
                (47.1, 8.0),
                (47.1, 8.1),
                (47.0, 8.1),
            ],
        )
        assert rule.id == "TEST_GEO"
        assert rule.type == "geofence_polygon"
        assert len(rule.polygon) == 4

    def test_polygon_with_options(self) -> None:
        """Test polygon with all options."""
        rule = GeofencePolygonRule(
            id="GEO",
            polygon=[(0, 0), (1, 0), (1, 1)],
            warn_buffer_m=15.0,
            on_warn="warn",
            on_violation="land",
        )
        assert rule.warn_buffer_m == 15.0
        assert rule.on_violation == "land"

    def test_invalid_latitude(self) -> None:
        """Test invalid latitude is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            GeofencePolygonRule(
                id="BAD",
                polygon=[(91.0, 0.0), (0, 0), (0, 1)],
            )
        assert "latitude" in str(exc_info.value).lower()

    def test_invalid_longitude(self) -> None:
        """Test invalid longitude is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            GeofencePolygonRule(
                id="BAD",
                polygon=[(0, 181.0), (0, 0), (0, 1)],
            )
        assert "longitude" in str(exc_info.value).lower()

    def test_insufficient_vertices(self) -> None:
        """Test polygon needs at least 3 vertices."""
        with pytest.raises(ValidationError):
            GeofencePolygonRule(
                id="BAD",
                polygon=[(0, 0), (1, 1)],
            )


class TestAltitudeMaxRule:
    """Tests for AltitudeMaxRule."""

    def test_valid_altitude_max(self) -> None:
        """Test valid altitude ceiling rule."""
        rule = AltitudeMaxRule(
            id="ALT_CEIL",
            max_relative_alt_m=100.0,
        )
        assert rule.max_relative_alt_m == 100.0
        assert rule.type == "altitude_max"

    def test_zero_altitude_rejected(self) -> None:
        """Test zero altitude ceiling is rejected."""
        with pytest.raises(ValidationError):
            AltitudeMaxRule(id="BAD", max_relative_alt_m=0)


class TestAltitudeMinRule:
    """Tests for AltitudeMinRule."""

    def test_valid_altitude_min(self) -> None:
        """Test valid altitude floor rule."""
        rule = AltitudeMinRule(
            id="ALT_FLOOR",
            min_relative_alt_m=5.0,
        )
        assert rule.min_relative_alt_m == 5.0

    def test_zero_altitude_allowed(self) -> None:
        """Test zero altitude floor is allowed."""
        rule = AltitudeMinRule(id="FLOOR", min_relative_alt_m=0)
        assert rule.min_relative_alt_m == 0


class TestBatteryMinRule:
    """Tests for BatteryMinRule."""

    def test_valid_battery_rule(self) -> None:
        """Test valid battery rule."""
        rule = BatteryMinRule(
            id="BAT_LOW",
            threshold_percent=20.0,
        )
        assert rule.threshold_percent == 20.0

    def test_battery_with_voltage(self) -> None:
        """Test battery rule with voltage floor."""
        rule = BatteryMinRule(
            id="BAT",
            threshold_percent=15.0,
            voltage_floor_v=10.5,
        )
        assert rule.voltage_floor_v == 10.5

    def test_invalid_threshold_range(self) -> None:
        """Test threshold must be in [0, 100]."""
        with pytest.raises(ValidationError):
            BatteryMinRule(id="BAD", threshold_percent=101)


class TestTelemetryMaxAgeRule:
    """Tests for TelemetryMaxAgeRule."""

    def test_valid_staleness_rule(self) -> None:
        """Test valid telemetry staleness rule."""
        rule = TelemetryMaxAgeRule(
            id="STALE",
            stream="position",
            max_age_s=2.0,
        )
        assert rule.stream == "position"
        assert rule.max_age_s == 2.0

    def test_invalid_stream(self) -> None:
        """Test invalid stream is rejected."""
        with pytest.raises(ValidationError):
            TelemetryMaxAgeRule(
                id="BAD",
                stream="invalid_stream",
                max_age_s=1.0,
            )

    def test_zero_max_age_rejected(self) -> None:
        """Test zero max age is rejected."""
        with pytest.raises(ValidationError):
            TelemetryMaxAgeRule(id="BAD", stream="position", max_age_s=0)


class TestConnectionLostRule:
    """Tests for ConnectionLostRule."""

    def test_valid_connection_rule(self) -> None:
        """Test valid connection lost rule."""
        rule = ConnectionLostRule(id="LINK")
        assert rule.type == "connection_lost"
        assert rule.on_violation == "log_only"

    def test_warn_action_allowed(self) -> None:
        """Test warn action is allowed."""
        rule = ConnectionLostRule(id="LINK", on_violation="warn")
        assert rule.on_violation == "warn"

    def test_rtl_action_not_allowed(self) -> None:
        """Test RTL action is not allowed for connection lost."""
        with pytest.raises(ValidationError):
            ConnectionLostRule(id="LINK", on_violation="rtl")


class TestRuleConfig:
    """Tests for RuleConfig."""

    def test_empty_config(self) -> None:
        """Test empty configuration is valid."""
        config = RuleConfig()
        assert config.version == 1
        assert config.vehicle_id == "default"
        assert config.tick_hz == 10.0
        assert len(config.rules) == 0

    def test_full_config(self) -> None:
        """Test full configuration."""
        config = RuleConfig(
            version=1,
            vehicle_id="test-drone",
            tick_hz=20.0,
            defaults=RuleDefaults(debounce_s=0.5),
            rules=[
                GeofencePolygonRule(
                    id="GEO",
                    polygon=[(0, 0), (1, 0), (1, 1)],
                ),
                BatteryMinRule(
                    id="BAT",
                    threshold_percent=20.0,
                ),
            ],
        )
        assert config.vehicle_id == "test-drone"
        assert len(config.rules) == 2

    def test_duplicate_rule_ids_rejected(self) -> None:
        """Test duplicate rule IDs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RuleConfig(
                rules=[
                    BatteryMinRule(id="SAME_ID", threshold_percent=20.0),
                    BatteryMinRule(id="SAME_ID", threshold_percent=10.0),
                ]
            )
        assert "duplicate" in str(exc_info.value).lower()

    def test_get_rule(self) -> None:
        """Test getting a rule by ID."""
        config = RuleConfig(
            rules=[
                BatteryMinRule(id="BAT1", threshold_percent=25.0),
                BatteryMinRule(id="BAT2", threshold_percent=10.0),
            ]
        )
        rule = config.get_rule("BAT1")
        assert rule is not None
        assert rule.id == "BAT1"
        assert config.get_rule("NONEXISTENT") is None

    def test_effective_debounce_from_default(self) -> None:
        """Test effective debounce uses defaults."""
        config = RuleConfig(
            defaults=RuleDefaults(debounce_s=2.0),
            rules=[BatteryMinRule(id="BAT", threshold_percent=20.0)],
        )
        rule = config.rules[0]
        assert config.get_effective_debounce(rule) == 2.0

    def test_effective_debounce_override(self) -> None:
        """Test effective debounce uses rule override."""
        config = RuleConfig(
            defaults=RuleDefaults(debounce_s=2.0),
            rules=[BatteryMinRule(id="BAT", threshold_percent=20.0, debounce_s=0.5)],
        )
        rule = config.rules[0]
        assert config.get_effective_debounce(rule) == 0.5

    def test_invalid_version(self) -> None:
        """Test invalid version is rejected."""
        with pytest.raises(ValidationError):
            RuleConfig(version=2)

    def test_invalid_tick_hz(self) -> None:
        """Test invalid tick rate is rejected."""
        with pytest.raises(ValidationError):
            RuleConfig(tick_hz=0)
        with pytest.raises(ValidationError):
            RuleConfig(tick_hz=101)
