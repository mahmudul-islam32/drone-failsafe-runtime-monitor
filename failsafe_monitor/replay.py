"""
Telemetry replay for testing.

Replays recorded telemetry data into the rule engine for deterministic testing
without requiring a live SITL connection.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    Battery,
    ConnectionState,
    FlightMode,
    Health,
    Position,
    VehicleState,
)

logger = logging.getLogger(__name__)


@dataclass
class TelemetryFrame:
    """A single frame of recorded telemetry."""

    timestamp_s: float
    position: Position | None = None
    battery: Battery | None = None
    health: Health | None = None
    flight_mode: FlightMode = FlightMode.UNKNOWN
    is_armed: bool = False
    connection_state: ConnectionState = ConnectionState.CONNECTED

    def to_vehicle_state(self) -> VehicleState:
        """Convert to a VehicleState for the rule engine."""
        return VehicleState(
            position=self.position,
            battery=self.battery,
            health=self.health,
            flight_mode=self.flight_mode,
            flight_mode_timestamp_s=self.timestamp_s,
            is_armed=self.is_armed,
            armed_timestamp_s=self.timestamp_s,
            connection_state=self.connection_state,
            connection_timestamp_s=self.timestamp_s,
        )


class TelemetryRecorder:
    """Records telemetry frames for later replay."""

    def __init__(self) -> None:
        self._frames: list[TelemetryFrame] = []
        self._start_time: float | None = None

    def record(self, state: VehicleState) -> None:
        """Record a vehicle state snapshot."""
        if self._start_time is None:
            self._start_time = time.time()

        frame = TelemetryFrame(
            timestamp_s=time.time(),
            position=state.position,
            battery=state.battery,
            health=state.health,
            flight_mode=state.flight_mode,
            is_armed=state.is_armed,
            connection_state=state.connection_state,
        )
        self._frames.append(frame)

    @property
    def frames(self) -> list[TelemetryFrame]:
        """Return all recorded frames."""
        return self._frames.copy()

    def save(self, path: Path | str) -> None:
        """Save recorded frames to a JSON file."""
        path = Path(path)

        data = []
        for frame in self._frames:
            frame_data: dict[str, Any] = {
                "timestamp_s": frame.timestamp_s,
                "flight_mode": frame.flight_mode.value,
                "is_armed": frame.is_armed,
                "connection_state": frame.connection_state.value,
            }

            if frame.position:
                frame_data["position"] = {
                    "latitude_deg": frame.position.latitude_deg,
                    "longitude_deg": frame.position.longitude_deg,
                    "absolute_altitude_m": frame.position.absolute_altitude_m,
                    "relative_altitude_m": frame.position.relative_altitude_m,
                    "timestamp_s": frame.position.timestamp_s,
                }

            if frame.battery:
                frame_data["battery"] = {
                    "remaining_percent": frame.battery.remaining_percent,
                    "voltage_v": frame.battery.voltage_v,
                    "timestamp_s": frame.battery.timestamp_s,
                }

            if frame.health:
                frame_data["health"] = {
                    "is_gyrometer_calibration_ok": frame.health.is_gyrometer_calibration_ok,
                    "is_accelerometer_calibration_ok": frame.health.is_accelerometer_calibration_ok,
                    "is_magnetometer_calibration_ok": frame.health.is_magnetometer_calibration_ok,
                    "is_local_position_ok": frame.health.is_local_position_ok,
                    "is_global_position_ok": frame.health.is_global_position_ok,
                    "is_home_position_ok": frame.health.is_home_position_ok,
                    "is_armable": frame.health.is_armable,
                    "timestamp_s": frame.health.timestamp_s,
                }

            data.append(frame_data)

        with open(path, "w", encoding="utf-8") as f:
            json.dump({"frames": data}, f, indent=2)

        logger.info("Saved %d frames to %s", len(self._frames), path)


class TelemetryPlayer:
    """Plays back recorded telemetry frames."""

    def __init__(self, frames: list[TelemetryFrame]) -> None:
        self._frames = frames
        self._index = 0

    @classmethod
    def from_file(cls, path: Path | str) -> TelemetryPlayer:
        """Load frames from a JSON file."""
        path = Path(path)

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        frames = []
        for frame_data in data.get("frames", []):
            position = None
            if "position" in frame_data:
                pos_data = frame_data["position"]
                position = Position(
                    latitude_deg=pos_data["latitude_deg"],
                    longitude_deg=pos_data["longitude_deg"],
                    absolute_altitude_m=pos_data["absolute_altitude_m"],
                    relative_altitude_m=pos_data["relative_altitude_m"],
                    timestamp_s=pos_data.get("timestamp_s", frame_data["timestamp_s"]),
                )

            battery = None
            if "battery" in frame_data:
                bat_data = frame_data["battery"]
                battery = Battery(
                    remaining_percent=bat_data["remaining_percent"],
                    voltage_v=bat_data.get("voltage_v"),
                    timestamp_s=bat_data.get("timestamp_s", frame_data["timestamp_s"]),
                )

            health = None
            if "health" in frame_data:
                h_data = frame_data["health"]
                health = Health(
                    is_gyrometer_calibration_ok=h_data.get("is_gyrometer_calibration_ok", True),
                    is_accelerometer_calibration_ok=h_data.get(
                        "is_accelerometer_calibration_ok", True
                    ),
                    is_magnetometer_calibration_ok=h_data.get(
                        "is_magnetometer_calibration_ok", True
                    ),
                    is_local_position_ok=h_data.get("is_local_position_ok", True),
                    is_global_position_ok=h_data.get("is_global_position_ok", True),
                    is_home_position_ok=h_data.get("is_home_position_ok", True),
                    is_armable=h_data.get("is_armable", True),
                    timestamp_s=h_data.get("timestamp_s", frame_data["timestamp_s"]),
                )

            frame = TelemetryFrame(
                timestamp_s=frame_data["timestamp_s"],
                position=position,
                battery=battery,
                health=health,
                flight_mode=FlightMode(frame_data.get("flight_mode", "unknown")),
                is_armed=frame_data.get("is_armed", False),
                connection_state=ConnectionState(
                    frame_data.get("connection_state", "connected")
                ),
            )
            frames.append(frame)

        logger.info("Loaded %d frames from %s", len(frames), path)
        return cls(frames)

    @classmethod
    def from_frames(cls, frames: list[TelemetryFrame]) -> TelemetryPlayer:
        """Create a player from a list of frames."""
        return cls(frames)

    @property
    def total_frames(self) -> int:
        """Return the total number of frames."""
        return len(self._frames)

    @property
    def current_index(self) -> int:
        """Return the current frame index."""
        return self._index

    @property
    def is_finished(self) -> bool:
        """Return True if all frames have been played."""
        return self._index >= len(self._frames)

    def reset(self) -> None:
        """Reset to the beginning."""
        self._index = 0

    def next_frame(self) -> TelemetryFrame | None:
        """Get the next frame, or None if finished."""
        if self._index >= len(self._frames):
            return None
        frame = self._frames[self._index]
        self._index += 1
        return frame

    def next_state(self) -> VehicleState | None:
        """Get the next frame as a VehicleState."""
        frame = self.next_frame()
        if frame is None:
            return None
        return frame.to_vehicle_state()


def create_test_scenario(
    name: str,
    duration_s: float,
    tick_hz: float = 10.0,
    **kwargs: Any,
) -> list[TelemetryFrame]:
    """
    Create a test scenario with generated telemetry frames.

    Args:
        name: Scenario name
        duration_s: Total duration in seconds
        tick_hz: Frame rate
        **kwargs: Scenario-specific parameters

    Returns:
        List of TelemetryFrame objects
    """
    frames = []
    num_frames = int(duration_s * tick_hz)
    start_time = time.time()

    lat = kwargs.get("start_lat", 47.398)
    lon = kwargs.get("start_lon", 8.546)
    alt = kwargs.get("start_alt", 10.0)
    battery = kwargs.get("start_battery", 80.0)

    for i in range(num_frames):
        t = start_time + (i / tick_hz)

        if name == "geofence_breach":
            breach_frame = int(num_frames * 0.3)
            if i >= breach_frame:
                lat += 0.001
                lon += 0.001

        elif name == "altitude_breach":
            breach_frame = int(num_frames * 0.3)
            if i >= breach_frame:
                alt = 80.0

        elif name == "battery_drain":
            battery = max(0, battery - (30.0 / num_frames))

        elif name == "gps_spike":
            spike_frame = int(num_frames * 0.5)
            if i == spike_frame:
                lat += 0.01

        position = Position(
            latitude_deg=lat,
            longitude_deg=lon,
            absolute_altitude_m=450 + alt,
            relative_altitude_m=alt,
            timestamp_s=t,
        )

        bat = Battery(
            remaining_percent=battery,
            voltage_v=11.0 + (battery / 100) * 1.6,
            timestamp_s=t,
        )

        frame = TelemetryFrame(
            timestamp_s=t,
            position=position,
            battery=bat,
            flight_mode=FlightMode.MISSION,
            is_armed=True,
            connection_state=ConnectionState.CONNECTED,
        )
        frames.append(frame)

    return frames
