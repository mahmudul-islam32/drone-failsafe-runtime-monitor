"""
Rule engine for evaluating safety rules against vehicle state.

Implements debouncing, hysteresis, and severity escalation.
"""

from __future__ import annotations

import logging
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pyproj
from shapely.geometry import Point, Polygon
from shapely.ops import transform

from .models import (
    ActionRequest,
    ConnectionState,
    MonitorState,
    Severity,
    VehicleState,
    Violation,
)
from .rules import (
    AltitudeMaxRule,
    AltitudeMinRule,
    BatteryMinRule,
    ConnectionLostRule,
    GeofencePolygonRule,
    Rule,
    RuleConfig,
    TelemetryMaxAgeRule,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "log_only": Severity.LOG_ONLY,
    "warn": Severity.WARN,
    "rtl": Severity.RTL,
    "land": Severity.LAND,
}


@dataclass
class RuleState:
    """Tracks the state of a single rule's violation."""

    rule_id: str
    is_violated: bool = False
    is_warning: bool = False
    violation_start_s: float | None = None
    last_violation: Violation | None = None
    debounce_met: bool = False


@dataclass
class EngineState:
    """Tracks the overall state of the rule engine."""

    monitor_state: MonitorState = MonitorState.CONNECTING
    highest_action: Severity = Severity.LOG_ONLY
    rule_states: dict[str, RuleState] = field(default_factory=dict)
    last_tick_s: float = 0.0
    flight_start_s: float | None = None
    is_latched: bool = False


class RuleEngine:
    """
    Evaluates safety rules against vehicle state.

    Implements:
    - Debouncing: violations must persist for debounce_s before triggering
    - Hysteresis: warnings clear when below threshold minus hysteresis
    - Escalation: severity only increases during a flight (warn -> rtl -> land)
    """

    def __init__(self, config: RuleConfig, clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self._clock = clock
        self._state = EngineState()
        self._geofence_polygons: dict[str, Polygon] = {}
        self._warn_polygons: dict[str, Polygon] = {}
        self._prepare_geofences()

    def _prepare_geofences(self) -> None:
        """Pre-compute Shapely polygons for geofence rules."""
        for rule in self.config.rules:
            if isinstance(rule, GeofencePolygonRule) and rule.enabled:
                coords = [(lon, lat) for lat, lon in rule.polygon]
                self._geofence_polygons[rule.id] = Polygon(coords)

                if rule.warn_buffer_m > 0:
                    buffered = self._buffer_polygon_meters(
                        self._geofence_polygons[rule.id],
                        -rule.warn_buffer_m,
                    )
                    self._warn_polygons[rule.id] = buffered

    def _buffer_polygon_meters(self, polygon: Polygon, distance_m: float) -> Polygon:
        """
        Buffer a polygon by a distance in meters.

        Negative distance shrinks the polygon (for warning buffer inside boundary).
        """
        centroid = polygon.centroid
        proj_string = (
            f"+proj=aeqd +lat_0={centroid.y} +lon_0={centroid.x} +datum=WGS84 +units=m"
        )
        project_to_m = pyproj.Transformer.from_crs(
            "EPSG:4326", proj_string, always_xy=True
        ).transform
        project_to_wgs = pyproj.Transformer.from_crs(
            proj_string, "EPSG:4326", always_xy=True
        ).transform

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            polygon_m = transform(project_to_m, polygon)
            buffered_m = polygon_m.buffer(distance_m)
            return transform(project_to_wgs, buffered_m)

    @property
    def state(self) -> EngineState:
        """Return the current engine state."""
        return self._state

    @property
    def monitor_state(self) -> MonitorState:
        """Return the current monitor state."""
        return self._state.monitor_state

    def reset(self) -> None:
        """Reset the engine state for a new flight."""
        self._state = EngineState()
        self._state.monitor_state = MonitorState.CONNECTING
        logger.info("Rule engine reset")

    def tick(self, vehicle_state: VehicleState) -> list[Violation]:
        """
        Evaluate all rules against the current vehicle state.

        Args:
            vehicle_state: Current vehicle state snapshot

        Returns:
            List of violations that have met their debounce requirements
        """
        now = self._clock()
        self._state.last_tick_s = now

        if vehicle_state.is_armed and self._state.flight_start_s is None:
            self._state.flight_start_s = now
            logger.info("Flight started")

        if not vehicle_state.is_armed and self._state.flight_start_s is not None:
            self._state.flight_start_s = None
            self._state.is_latched = False
            self._state.highest_action = Severity.LOG_ONLY
            self._state.monitor_state = MonitorState.MONITORING
            logger.info("Flight ended, latch released")

        if vehicle_state.connection_state == ConnectionState.DISCONNECTED:
            if self._state.monitor_state != MonitorState.BLIND:
                self._state.monitor_state = MonitorState.BLIND
                logger.warning("Monitor is BLIND - connection lost")
            return self._evaluate_connection_rules(vehicle_state, now)

        if (
            self._state.monitor_state == MonitorState.CONNECTING
            and vehicle_state.connection_state == ConnectionState.CONNECTED
        ):
            self._state.monitor_state = MonitorState.MONITORING
            logger.info("Monitor state: MONITORING")

        violations = []

        for rule in self.config.rules:
            if not rule.enabled:
                continue

            rule_violations = self._evaluate_rule(rule, vehicle_state, now)
            violations.extend(rule_violations)

        self._update_monitor_state(violations)

        return violations

    def _evaluate_rule(
        self, rule: Rule, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate a single rule and return any violations."""
        if isinstance(rule, GeofencePolygonRule):
            return self._evaluate_geofence(rule, state, now)
        elif isinstance(rule, AltitudeMaxRule):
            return self._evaluate_altitude_max(rule, state, now)
        elif isinstance(rule, AltitudeMinRule):
            return self._evaluate_altitude_min(rule, state, now)
        elif isinstance(rule, BatteryMinRule):
            return self._evaluate_battery(rule, state, now)
        elif isinstance(rule, TelemetryMaxAgeRule):
            return self._evaluate_telemetry_age(rule, state, now)
        elif isinstance(rule, ConnectionLostRule):
            return self._evaluate_connection(rule, state, now)
        return []

    def _evaluate_geofence(
        self, rule: GeofencePolygonRule, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate geofence polygon rule."""
        if state.position is None:
            return []

        point = Point(state.position.longitude_deg, state.position.latitude_deg)
        polygon = self._geofence_polygons.get(rule.id)
        if polygon is None:
            return []

        violations = []
        rule_state = self._get_or_create_rule_state(rule.id)

        is_inside = polygon.contains(point)
        is_in_warning_zone = False

        if is_inside and rule.id in self._warn_polygons:
            warn_polygon = self._warn_polygons[rule.id]
            is_in_warning_zone = not warn_polygon.contains(point)

        if not is_inside:
            violation = self._handle_violation(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_violation],
                f"Outside geofence {rule.id}",
                {
                    "lat": state.position.latitude_deg,
                    "lon": state.position.longitude_deg,
                },
                now,
            )
            if violation:
                violations.append(violation)
        elif is_in_warning_zone:
            violation = self._handle_warning(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_warn],
                f"Near geofence boundary {rule.id}",
                {
                    "lat": state.position.latitude_deg,
                    "lon": state.position.longitude_deg,
                },
                now,
            )
            if violation:
                violations.append(violation)
        else:
            self._clear_violation(rule, rule_state, now)

        return violations

    def _evaluate_altitude_max(
        self, rule: AltitudeMaxRule, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate altitude ceiling rule."""
        if state.position is None:
            return []

        alt = state.position.relative_altitude_m
        rule_state = self._get_or_create_rule_state(rule.id)
        violations = []

        if alt > rule.max_relative_alt_m:
            violation = self._handle_violation(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_violation],
                f"Above altitude ceiling {rule.max_relative_alt_m}m",
                {"altitude_m": alt, "max_m": rule.max_relative_alt_m},
                now,
            )
            if violation:
                violations.append(violation)
        elif rule.warn_buffer_m > 0 and alt > (rule.max_relative_alt_m - rule.warn_buffer_m):
            violation = self._handle_warning(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_warn],
                f"Near altitude ceiling {rule.max_relative_alt_m}m",
                {"altitude_m": alt, "max_m": rule.max_relative_alt_m},
                now,
            )
            if violation:
                violations.append(violation)
        else:
            self._clear_violation(rule, rule_state, now)

        return violations

    def _evaluate_altitude_min(
        self, rule: AltitudeMinRule, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate altitude floor rule."""
        if state.position is None:
            return []

        alt = state.position.relative_altitude_m
        rule_state = self._get_or_create_rule_state(rule.id)
        violations = []

        if alt < rule.min_relative_alt_m:
            violation = self._handle_violation(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_violation],
                f"Below altitude floor {rule.min_relative_alt_m}m",
                {"altitude_m": alt, "min_m": rule.min_relative_alt_m},
                now,
            )
            if violation:
                violations.append(violation)
        else:
            self._clear_violation(rule, rule_state, now)

        return violations

    def _evaluate_battery(
        self, rule: BatteryMinRule, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate battery threshold rule."""
        if state.battery is None:
            return []

        remaining = state.battery.remaining_percent
        rule_state = self._get_or_create_rule_state(rule.id)
        violations = []

        threshold_violated = remaining < rule.threshold_percent
        voltage_violated = False
        if rule.voltage_floor_v and state.battery.voltage_v:
            voltage_violated = state.battery.voltage_v < rule.voltage_floor_v

        if threshold_violated or voltage_violated:
            violation = self._handle_violation(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_violation],
                f"Battery below {rule.threshold_percent}%",
                {
                    "remaining_percent": remaining,
                    "threshold_percent": rule.threshold_percent,
                    "voltage_v": state.battery.voltage_v,
                },
                now,
            )
            if violation:
                violations.append(violation)
        else:
            self._clear_violation(rule, rule_state, now)

        return violations

    def _evaluate_telemetry_age(
        self, rule: TelemetryMaxAgeRule, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate telemetry staleness rule."""
        rule_state = self._get_or_create_rule_state(rule.id)
        violations = []

        age: float | None = None
        if rule.stream == "position" and state.position:
            age = now - state.position.timestamp_s
        elif rule.stream == "battery" and state.battery:
            age = now - state.battery.timestamp_s
        elif rule.stream == "health" and state.health:
            age = now - state.health.timestamp_s

        if age is None:
            violation = self._handle_violation(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_violation],
                f"No {rule.stream} telemetry available",
                {"stream": rule.stream, "max_age_s": rule.max_age_s},
                now,
            )
            if violation:
                violations.append(violation)
        elif age > rule.max_age_s:
            violation = self._handle_violation(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_violation],
                f"Stale {rule.stream} telemetry ({age:.1f}s > {rule.max_age_s}s)",
                {"stream": rule.stream, "age_s": age, "max_age_s": rule.max_age_s},
                now,
            )
            if violation:
                violations.append(violation)
        else:
            self._clear_violation(rule, rule_state, now)

        return violations

    def _evaluate_connection(
        self, rule: ConnectionLostRule, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate connection loss rule."""
        rule_state = self._get_or_create_rule_state(rule.id)
        violations = []

        if state.connection_state == ConnectionState.DISCONNECTED:
            violation = self._handle_violation(
                rule,
                rule_state,
                SEVERITY_MAP[rule.on_violation],
                "MAVLink connection lost - monitor is BLIND",
                {"connection_state": state.connection_state.value},
                now,
            )
            if violation:
                violations.append(violation)
        else:
            self._clear_violation(rule, rule_state, now)

        return violations

    def _evaluate_connection_rules(
        self, state: VehicleState, now: float
    ) -> list[Violation]:
        """Evaluate only connection-related rules when disconnected."""
        violations = []
        for rule in self.config.rules:
            if isinstance(rule, ConnectionLostRule) and rule.enabled:
                violations.extend(self._evaluate_connection(rule, state, now))
        return violations

    def _get_or_create_rule_state(self, rule_id: str) -> RuleState:
        """Get or create a rule state tracker."""
        if rule_id not in self._state.rule_states:
            self._state.rule_states[rule_id] = RuleState(rule_id=rule_id)
        return self._state.rule_states[rule_id]

    def _handle_violation(
        self,
        rule: Rule,
        rule_state: RuleState,
        severity: Severity,
        message: str,
        values: dict,
        now: float,
    ) -> Violation | None:
        """
        Handle a rule violation with debouncing.

        Returns a Violation if the debounce requirement is met.
        """
        debounce_s = self.config.get_effective_debounce(rule)

        if not rule_state.is_violated:
            rule_state.is_violated = True
            rule_state.violation_start_s = now
            rule_state.debounce_met = False
            logger.debug("Violation started: %s", rule.id)

        elapsed = now - (rule_state.violation_start_s or now)

        if elapsed >= debounce_s and not rule_state.debounce_met:
            rule_state.debounce_met = True
            effective_severity = self._apply_escalation(severity)

            violation = Violation(
                rule_id=rule.id,
                severity=effective_severity,
                message=message,
                values=values,
                timestamp_s=now,
                debounce_start_s=rule_state.violation_start_s,
            )
            rule_state.last_violation = violation

            logger.info(
                "Violation confirmed: %s (severity=%s, elapsed=%.1fs)",
                rule.id,
                effective_severity.value,
                elapsed,
            )
            return violation

        return None

    def _handle_warning(
        self,
        rule: Rule,
        rule_state: RuleState,
        severity: Severity,
        message: str,
        values: dict,
        now: float,
    ) -> Violation | None:
        """Handle a warning condition."""
        if not rule_state.is_warning:
            rule_state.is_warning = True
            logger.debug("Warning started: %s", rule.id)

        if severity <= Severity.WARN:
            violation = Violation(
                rule_id=rule.id,
                severity=severity,
                message=message,
                values=values,
                timestamp_s=now,
            )
            rule_state.last_violation = violation
            return violation

        return None

    def _clear_violation(self, rule: Rule, rule_state: RuleState, _now: float) -> None:
        """Clear a violation with hysteresis check."""
        if rule_state.is_violated or rule_state.is_warning:
            rule_state.is_violated = False
            rule_state.is_warning = False
            rule_state.violation_start_s = None
            rule_state.debounce_met = False
            logger.debug("Violation cleared: %s", rule.id)

    def _apply_escalation(self, severity: Severity) -> Severity:
        """
        Apply escalation logic - severity only increases during a flight.

        Returns the effective severity after applying the latch.
        """
        if self._state.flight_start_s is None:
            return severity

        if severity > self._state.highest_action:
            self._state.highest_action = severity
            self._state.is_latched = True
            logger.info("Escalating to severity: %s", severity.value)

        return max(severity, self._state.highest_action)

    def _update_monitor_state(self, violations: list[Violation]) -> None:
        """Update the monitor state machine based on violations."""
        if not violations:
            if self._state.is_latched:
                return
            pending = any(
                rs.is_violated or rs.is_warning for rs in self._state.rule_states.values()
            )
            if pending and self._state.monitor_state == MonitorState.MONITORING:
                # A violation is still being debounced: keep showing a warning.
                self._state.monitor_state = MonitorState.WARNING
            elif not pending and self._state.monitor_state == MonitorState.WARNING:
                self._state.monitor_state = MonitorState.MONITORING
            return

        highest_severity = max(v.severity for v in violations)

        if highest_severity >= Severity.LAND:
            self._state.monitor_state = MonitorState.LAND
        elif highest_severity >= Severity.RTL:
            self._state.monitor_state = MonitorState.RTL
        elif (
            highest_severity >= Severity.WARN
            and self._state.monitor_state not in (MonitorState.RTL, MonitorState.LAND)
        ):
            self._state.monitor_state = MonitorState.WARNING

    def record_executed_action(self, action: Severity) -> None:
        """
        Inform the engine of the action actually executed.

        The executor may escalate (e.g. RTL not confirmed -> Land); this keeps the
        latch and monitor state consistent with what the vehicle was told to do.
        """
        if action < Severity.RTL:
            return
        self._apply_escalation(action)
        if action >= Severity.LAND:
            self._state.monitor_state = MonitorState.LAND
        elif self._state.monitor_state != MonitorState.LAND:
            self._state.monitor_state = MonitorState.RTL

    def get_action_request(self, violations: list[Violation]) -> ActionRequest | None:
        """
        Get an action request based on current violations.

        Returns the highest severity action that should be executed.
        """
        if not violations:
            return None

        highest_violation = max(violations, key=lambda v: v.severity)

        if highest_violation.severity < Severity.RTL:
            return None

        return ActionRequest(
            action=highest_violation.severity,
            source_rule_id=highest_violation.rule_id,
            source_violation=highest_violation,
        )
