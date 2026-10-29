"""
Data models for the failsafe monitor.

Defines the core data structures: vehicle state snapshots, rule definitions,
violation events, and action requests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Action severity levels, ordered from lowest to highest."""

    LOG_ONLY = "log_only"
    WARN = "warn"
    RTL = "rtl"
    LAND = "land"

    def __lt__(self, other: Severity) -> bool:
        order = [Severity.LOG_ONLY, Severity.WARN, Severity.RTL, Severity.LAND]
        return order.index(self) < order.index(other)

    def __le__(self, other: Severity) -> bool:
        return self == other or self < other

    def __gt__(self, other: Severity) -> bool:
        return not self <= other

    def __ge__(self, other: Severity) -> bool:
        return not self < other


class FlightMode(str, Enum):
    """PX4 flight modes relevant to the monitor."""

    UNKNOWN = "unknown"
    READY = "ready"
    TAKEOFF = "takeoff"
    HOLD = "hold"
    MISSION = "mission"
    RETURN_TO_LAUNCH = "return_to_launch"
    LAND = "land"
    OFFBOARD = "offboard"
    FOLLOW_ME = "follow_me"
    MANUAL = "manual"
    ALTCTL = "altctl"
    POSCTL = "posctl"
    ACRO = "acro"
    STABILIZED = "stabilized"
    RATTITUDE = "rattitude"


class ConnectionState(str, Enum):
    """MAVLink connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class MonitorState(str, Enum):
    """Monitor state machine states."""

    CONNECTING = "connecting"
    MONITORING = "monitoring"
    WARNING = "warning"
    RTL = "rtl"
    LAND = "land"
    BLIND = "blind"
    DONE = "done"


@dataclass
class Position:
    """Geographic position with altitude."""

    latitude_deg: float
    longitude_deg: float
    absolute_altitude_m: float
    relative_altitude_m: float
    timestamp_s: float = field(default_factory=time.time)


@dataclass
class Battery:
    """Battery state."""

    remaining_percent: float
    voltage_v: float | None = None
    timestamp_s: float = field(default_factory=time.time)


@dataclass
class Health:
    """Vehicle health indicators."""

    is_gyrometer_calibration_ok: bool = True
    is_accelerometer_calibration_ok: bool = True
    is_magnetometer_calibration_ok: bool = True
    is_local_position_ok: bool = True
    is_global_position_ok: bool = True
    is_home_position_ok: bool = True
    is_armable: bool = True
    timestamp_s: float = field(default_factory=time.time)


@dataclass
class VehicleState:
    """
    Complete vehicle state snapshot.

    All telemetry data with individual timestamps for staleness detection.
    """

    position: Position | None = None
    battery: Battery | None = None
    health: Health | None = None
    flight_mode: FlightMode = FlightMode.UNKNOWN
    flight_mode_timestamp_s: float = field(default_factory=time.time)
    is_armed: bool = False
    armed_timestamp_s: float = field(default_factory=time.time)
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    connection_timestamp_s: float = field(default_factory=time.time)

    def position_age_s(self) -> float | None:
        """Return the age of the position data in seconds, or None if no position."""
        if self.position is None:
            return None
        return time.time() - self.position.timestamp_s


@dataclass
class Violation:
    """A rule violation detected by the rule engine."""

    rule_id: str
    severity: Severity
    message: str
    values: dict[str, Any] = field(default_factory=dict)
    timestamp_s: float = field(default_factory=time.time)
    debounce_start_s: float | None = None

    def debounce_elapsed_s(self) -> float:
        """Return time since debounce started."""
        if self.debounce_start_s is None:
            return 0.0
        return time.time() - self.debounce_start_s


@dataclass
class ActionRequest:
    """A request to execute a failsafe action."""

    action: Severity
    source_rule_id: str
    source_violation: Violation
    timestamp_s: float = field(default_factory=time.time)


@dataclass
class ActionResult:
    """Result of executing a failsafe action."""

    request: ActionRequest
    success: bool
    confirmed: bool = False
    retry_count: int = 0
    latency_s: float = 0.0
    error_message: str | None = None
    timestamp_s: float = field(default_factory=time.time)
