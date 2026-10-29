"""Tests for telemetry subscriber."""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from failsafe_monitor.models import ConnectionState, FlightMode
from failsafe_monitor.telemetry import (
    MAVSDK_TO_FLIGHT_MODE,
    TelemetryConfig,
    TelemetrySubscriber,
)


class TestTelemetryConfig:
    """Tests for TelemetryConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = TelemetryConfig()
        assert config.connection_url == "udp://:14540"
        assert config.connection_timeout_s == 30.0
        assert config.telemetry_timeout_s == 10.0

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = TelemetryConfig(
            connection_url="udp://:14550",
            connection_timeout_s=60.0,
        )
        assert config.connection_url == "udp://:14550"
        assert config.connection_timeout_s == 60.0


class TestTelemetrySubscriber:
    """Tests for TelemetrySubscriber."""

    def test_initial_state(self) -> None:
        """Test initial subscriber state."""
        subscriber = TelemetrySubscriber()
        assert subscriber.state is not None
        assert subscriber.state.connection_state == ConnectionState.DISCONNECTED
        assert subscriber.is_connected is False
        assert subscriber.system is None

    def test_add_remove_callback(self) -> None:
        """Test callback registration."""
        subscriber = TelemetrySubscriber()
        callback = MagicMock()

        subscriber.add_callback(callback)
        assert callback in subscriber._callbacks

        subscriber.remove_callback(callback)
        assert callback not in subscriber._callbacks

    def test_remove_nonexistent_callback(self) -> None:
        """Test removing callback that was not added."""
        subscriber = TelemetrySubscriber()
        callback = MagicMock()
        subscriber.remove_callback(callback)


class TestFlightModeMapping:
    """Tests for flight mode mapping."""

    def test_all_modes_mapped(self) -> None:
        """Test that common flight modes are mapped."""
        from mavsdk.telemetry import FlightMode as MavFlightMode

        expected_mappings = [
            (MavFlightMode.UNKNOWN, FlightMode.UNKNOWN),
            (MavFlightMode.READY, FlightMode.READY),
            (MavFlightMode.TAKEOFF, FlightMode.TAKEOFF),
            (MavFlightMode.HOLD, FlightMode.HOLD),
            (MavFlightMode.MISSION, FlightMode.MISSION),
            (MavFlightMode.RETURN_TO_LAUNCH, FlightMode.RETURN_TO_LAUNCH),
            (MavFlightMode.LAND, FlightMode.LAND),
            (MavFlightMode.OFFBOARD, FlightMode.OFFBOARD),
            (MavFlightMode.MANUAL, FlightMode.MANUAL),
        ]

        for mav_mode, expected in expected_mappings:
            assert MAVSDK_TO_FLIGHT_MODE[mav_mode] == expected

    def test_unknown_mode_fallback(self) -> None:
        """Test that unknown modes return UNKNOWN."""
        result = MAVSDK_TO_FLIGHT_MODE.get("invalid_mode", FlightMode.UNKNOWN)
        assert result == FlightMode.UNKNOWN


class TestTelemetrySubscriberAsync:
    """Async tests for TelemetrySubscriber."""

    @pytest.fixture
    def mock_system(self) -> MagicMock:
        """Create a mock MAVSDK System."""
        system = MagicMock()
        system.connect = AsyncMock()

        async def mock_connection_state():
            state = MagicMock()
            state.is_connected = True
            state.uuid = 12345
            yield state

        system.core.connection_state = mock_connection_state
        return system

    @pytest.mark.asyncio
    async def test_connect_success(self, mock_system: MagicMock) -> None:
        """Test successful connection."""
        subscriber = TelemetrySubscriber()

        with patch("failsafe_monitor.telemetry.System", return_value=mock_system):
            result = await subscriber.connect()

        assert result is True
        assert subscriber.is_connected is True
        assert subscriber.state.connection_state == ConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_connect_timeout(self) -> None:
        """Test connection timeout."""
        config = TelemetryConfig(connection_timeout_s=0.1)
        subscriber = TelemetrySubscriber(config)

        mock_system = MagicMock()
        mock_system.connect = AsyncMock()

        async def slow_connection_state():
            await asyncio.sleep(10)
            yield MagicMock(is_connected=True)

        mock_system.core.connection_state = slow_connection_state

        with patch("failsafe_monitor.telemetry.System", return_value=mock_system):
            result = await subscriber.connect()

        assert result is False
        assert subscriber.is_connected is False
        assert subscriber.state.connection_state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_connect_exception(self) -> None:
        """Test connection with exception."""
        subscriber = TelemetrySubscriber()

        mock_system = MagicMock()
        mock_system.connect = AsyncMock(side_effect=Exception("Connection failed"))

        with patch("failsafe_monitor.telemetry.System", return_value=mock_system):
            result = await subscriber.connect()

        assert result is False
        assert subscriber.state.connection_state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_start_without_connection_raises(self) -> None:
        """Test starting without connection raises error."""
        subscriber = TelemetrySubscriber()

        with pytest.raises(RuntimeError, match="Not connected"):
            await subscriber.start()

    @pytest.mark.asyncio
    async def test_callback_notification(self, mock_system: MagicMock) -> None:
        """Test that callbacks are notified on state changes."""
        subscriber = TelemetrySubscriber()
        callback = MagicMock()
        subscriber.add_callback(callback)

        with patch("failsafe_monitor.telemetry.System", return_value=mock_system):
            await subscriber.connect()

        assert callback.called

    @pytest.mark.asyncio
    async def test_callback_error_handling(self, mock_system: MagicMock) -> None:
        """Test that callback errors don't crash the subscriber."""
        subscriber = TelemetrySubscriber()
        bad_callback = MagicMock(side_effect=Exception("Callback error"))
        good_callback = MagicMock()

        subscriber.add_callback(bad_callback)
        subscriber.add_callback(good_callback)

        with patch("failsafe_monitor.telemetry.System", return_value=mock_system):
            await subscriber.connect()

        assert bad_callback.called
        assert good_callback.called

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self, mock_system: MagicMock) -> None:
        """Test that stop cancels all tasks."""
        subscriber = TelemetrySubscriber()

        async def mock_position():
            while True:
                await asyncio.sleep(0.1)
                yield MagicMock(
                    latitude_deg=0,
                    longitude_deg=0,
                    absolute_altitude_m=0,
                    relative_altitude_m=0,
                )

        mock_system.telemetry.position = mock_position
        mock_system.telemetry.battery = AsyncMock(return_value=iter([]))
        mock_system.telemetry.health = AsyncMock(return_value=iter([]))
        mock_system.telemetry.flight_mode = AsyncMock(return_value=iter([]))
        mock_system.telemetry.armed = AsyncMock(return_value=iter([]))

        with patch("failsafe_monitor.telemetry.System", return_value=mock_system):
            await subscriber.connect()
            await subscriber.start()

        assert len(subscriber._tasks) > 0
        assert subscriber._running is True

        await subscriber.stop()

        assert subscriber._running is False
        assert len(subscriber._tasks) == 0


class TestBatteryNormalization:
    """Tests for battery percentage normalization."""

    @pytest.mark.asyncio
    async def test_battery_normalization_0_to_1(self) -> None:
        """Test that battery values in 0-1 range are normalized to 0-100."""
        subscriber = TelemetrySubscriber()
        subscriber._running = True
        subscriber._state_lock = asyncio.Lock()

        mock_system = MagicMock()

        async def mock_battery():
            bat = MagicMock()
            bat.remaining_percent = 0.75
            bat.voltage_v = 12.0
            yield bat

        mock_system.telemetry.battery = mock_battery
        subscriber._system = mock_system

        task = asyncio.create_task(subscriber._subscribe_battery())
        await asyncio.sleep(0.01)
        subscriber._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert subscriber.state.battery is not None
        assert subscriber.state.battery.remaining_percent == 75.0

    @pytest.mark.asyncio
    async def test_battery_normalization_0_to_100(self) -> None:
        """Test that battery values already in 0-100 range are preserved."""
        subscriber = TelemetrySubscriber()
        subscriber._running = True
        subscriber._state_lock = asyncio.Lock()

        mock_system = MagicMock()

        async def mock_battery():
            bat = MagicMock()
            bat.remaining_percent = 75.0
            bat.voltage_v = 12.0
            yield bat

        mock_system.telemetry.battery = mock_battery
        subscriber._system = mock_system

        task = asyncio.create_task(subscriber._subscribe_battery())
        await asyncio.sleep(0.01)
        subscriber._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert subscriber.state.battery is not None
        assert subscriber.state.battery.remaining_percent == 75.0
