"""
Telemetry subscriber for MAVSDK connection.

Connects to PX4 SITL and subscribes to telemetry streams,
maintaining a timestamped vehicle state snapshot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from mavsdk import System
from mavsdk.telemetry import FlightMode as MavFlightMode

from .models import (
    Battery,
    ConnectionState,
    FlightMode,
    Health,
    Position,
    VehicleState,
)

logger = logging.getLogger(__name__)

MAVSDK_TO_FLIGHT_MODE: dict[MavFlightMode, FlightMode] = {
    MavFlightMode.UNKNOWN: FlightMode.UNKNOWN,
    MavFlightMode.READY: FlightMode.READY,
    MavFlightMode.TAKEOFF: FlightMode.TAKEOFF,
    MavFlightMode.HOLD: FlightMode.HOLD,
    MavFlightMode.MISSION: FlightMode.MISSION,
    MavFlightMode.RETURN_TO_LAUNCH: FlightMode.RETURN_TO_LAUNCH,
    MavFlightMode.LAND: FlightMode.LAND,
    MavFlightMode.OFFBOARD: FlightMode.OFFBOARD,
    MavFlightMode.FOLLOW_ME: FlightMode.FOLLOW_ME,
    MavFlightMode.MANUAL: FlightMode.MANUAL,
    MavFlightMode.ALTCTL: FlightMode.ALTCTL,
    MavFlightMode.POSCTL: FlightMode.POSCTL,
    MavFlightMode.ACRO: FlightMode.ACRO,
    MavFlightMode.STABILIZED: FlightMode.STABILIZED,
    MavFlightMode.RATTITUDE: FlightMode.RATTITUDE,
}


@dataclass
class TelemetryConfig:
    """Configuration for the telemetry subscriber."""

    connection_url: str = "udp://:14540"
    connection_timeout_s: float = 30.0
    telemetry_timeout_s: float = 10.0


StateUpdateCallback = Callable[[VehicleState], None]


class TelemetrySubscriber:
    """
    Manages MAVSDK connection and telemetry subscriptions.

    Maintains a current vehicle state snapshot that is updated by
    async telemetry streams. Callbacks can be registered to receive
    state updates.
    """

    def __init__(self, config: TelemetryConfig | None = None) -> None:
        self.config = config or TelemetryConfig()
        self._system: System | None = None
        self._state = VehicleState()
        self._state_lock = asyncio.Lock()
        self._callbacks: list[StateUpdateCallback] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._connected = False

    @property
    def state(self) -> VehicleState:
        """Return the current vehicle state snapshot."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Return True if connected to the vehicle."""
        return self._connected

    @property
    def system(self) -> System | None:
        """Return the MAVSDK System object."""
        return self._system

    def add_callback(self, callback: StateUpdateCallback) -> None:
        """Register a callback for state updates."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: StateUpdateCallback) -> None:
        """Remove a registered callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def _notify_callbacks(self) -> None:
        """Notify all callbacks of state change."""
        for callback in self._callbacks:
            try:
                callback(self._state)
            except Exception as e:
                logger.error("Callback error: %s", e)

    async def connect(self) -> bool:
        """
        Connect to the vehicle.

        Returns:
            True if connection successful, False otherwise.
        """
        self._system = System()

        logger.info("Connecting to %s...", self.config.connection_url)

        try:
            await self._system.connect(system_address=self.config.connection_url)
        except Exception as e:
            logger.error("Failed to initiate connection: %s", e)
            async with self._state_lock:
                self._state.connection_state = ConnectionState.DISCONNECTED
                self._state.connection_timestamp_s = time.time()
            return False

        async with self._state_lock:
            self._state.connection_state = ConnectionState.CONNECTING
            self._state.connection_timestamp_s = time.time()

        try:
            async with asyncio.timeout(self.config.connection_timeout_s):
                async for state in self._system.core.connection_state():
                    if state.is_connected:
                        logger.info("Connected to vehicle (UUID: %s)", state.uuid)
                        self._connected = True
                        async with self._state_lock:
                            self._state.connection_state = ConnectionState.CONNECTED
                            self._state.connection_timestamp_s = time.time()
                        await self._notify_callbacks()
                        return True
        except asyncio.TimeoutError:
            logger.error("Connection timeout after %.1fs", self.config.connection_timeout_s)
            async with self._state_lock:
                self._state.connection_state = ConnectionState.DISCONNECTED
                self._state.connection_timestamp_s = time.time()
            return False

        return False

    async def start(self) -> None:
        """Start all telemetry subscriptions."""
        if not self._system or not self._connected:
            raise RuntimeError("Not connected. Call connect() first.")

        if self._running:
            return

        self._running = True
        logger.info("Starting telemetry subscriptions...")

        self._tasks = [
            asyncio.create_task(self._subscribe_position(), name="position"),
            asyncio.create_task(self._subscribe_battery(), name="battery"),
            asyncio.create_task(self._subscribe_health(), name="health"),
            asyncio.create_task(self._subscribe_flight_mode(), name="flight_mode"),
            asyncio.create_task(self._subscribe_armed(), name="armed"),
            asyncio.create_task(self._subscribe_connection_state(), name="connection"),
        ]

    async def stop(self) -> None:
        """Stop all telemetry subscriptions."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        logger.info("Telemetry subscriptions stopped")

    async def _subscribe_position(self) -> None:
        """Subscribe to position telemetry."""
        if not self._system:
            return
        try:
            async for pos in self._system.telemetry.position():
                if not self._running:
                    break
                async with self._state_lock:
                    self._state.position = Position(
                        latitude_deg=pos.latitude_deg,
                        longitude_deg=pos.longitude_deg,
                        absolute_altitude_m=pos.absolute_altitude_m,
                        relative_altitude_m=pos.relative_altitude_m,
                        timestamp_s=time.time(),
                    )
                await self._notify_callbacks()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Position subscription error: %s", e)

    async def _subscribe_battery(self) -> None:
        """Subscribe to battery telemetry."""
        if not self._system:
            return
        try:
            async for bat in self._system.telemetry.battery():
                if not self._running:
                    break
                remaining = bat.remaining_percent
                if remaining <= 1.0:
                    remaining *= 100.0
                async with self._state_lock:
                    self._state.battery = Battery(
                        remaining_percent=remaining,
                        voltage_v=bat.voltage_v,
                        timestamp_s=time.time(),
                    )
                await self._notify_callbacks()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Battery subscription error: %s", e)

    async def _subscribe_health(self) -> None:
        """Subscribe to health telemetry."""
        if not self._system:
            return
        try:
            async for health in self._system.telemetry.health():
                if not self._running:
                    break
                async with self._state_lock:
                    self._state.health = Health(
                        is_gyrometer_calibration_ok=health.is_gyrometer_calibration_ok,
                        is_accelerometer_calibration_ok=health.is_accelerometer_calibration_ok,
                        is_magnetometer_calibration_ok=health.is_magnetometer_calibration_ok,
                        is_local_position_ok=health.is_local_position_ok,
                        is_global_position_ok=health.is_global_position_ok,
                        is_home_position_ok=health.is_home_position_ok,
                        is_armable=health.is_armable,
                        timestamp_s=time.time(),
                    )
                await self._notify_callbacks()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Health subscription error: %s", e)

    async def _subscribe_flight_mode(self) -> None:
        """Subscribe to flight mode telemetry."""
        if not self._system:
            return
        try:
            async for mode in self._system.telemetry.flight_mode():
                if not self._running:
                    break
                async with self._state_lock:
                    self._state.flight_mode = MAVSDK_TO_FLIGHT_MODE.get(mode, FlightMode.UNKNOWN)
                    self._state.flight_mode_timestamp_s = time.time()
                await self._notify_callbacks()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Flight mode subscription error: %s", e)

    async def _subscribe_armed(self) -> None:
        """Subscribe to armed state telemetry."""
        if not self._system:
            return
        try:
            async for is_armed in self._system.telemetry.armed():
                if not self._running:
                    break
                async with self._state_lock:
                    self._state.is_armed = is_armed
                    self._state.armed_timestamp_s = time.time()
                await self._notify_callbacks()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Armed subscription error: %s", e)

    async def _subscribe_connection_state(self) -> None:
        """Subscribe to connection state changes."""
        if not self._system:
            return
        try:
            async for state in self._system.core.connection_state():
                if not self._running:
                    break
                self._connected = state.is_connected
                async with self._state_lock:
                    if state.is_connected:
                        self._state.connection_state = ConnectionState.CONNECTED
                    else:
                        self._state.connection_state = ConnectionState.DISCONNECTED
                        logger.warning("Connection lost!")
                    self._state.connection_timestamp_s = time.time()
                await self._notify_callbacks()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Connection state subscription error: %s", e)


async def create_telemetry_subscriber(
    connection_url: str = "udp://:14540",
    timeout_s: float = 30.0,
) -> TelemetrySubscriber | None:
    """
    Create and connect a telemetry subscriber.

    Args:
        connection_url: MAVSDK connection URL
        timeout_s: Connection timeout in seconds

    Returns:
        Connected TelemetrySubscriber or None on failure.
    """
    config = TelemetryConfig(
        connection_url=connection_url,
        connection_timeout_s=timeout_s,
    )
    subscriber = TelemetrySubscriber(config)

    if await subscriber.connect():
        await subscriber.start()
        return subscriber

    return None
