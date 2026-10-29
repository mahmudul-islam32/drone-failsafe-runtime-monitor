"""Tests for telemetry replay functionality."""

import tempfile
import time
from pathlib import Path

import pytest

from failsafe_monitor.models import (
    Battery,
    ConnectionState,
    FlightMode,
    Health,
    Position,
    VehicleState,
)
from failsafe_monitor.replay import (
    TelemetryFrame,
    TelemetryPlayer,
    TelemetryRecorder,
    create_test_scenario,
)


class TestTelemetryFrame:
    """Tests for TelemetryFrame."""

    def test_frame_creation(self) -> None:
        """Test creating a frame."""
        frame = TelemetryFrame(
            timestamp_s=1000.0,
            position=Position(
                latitude_deg=47.0,
                longitude_deg=8.0,
                absolute_altitude_m=450.0,
                relative_altitude_m=10.0,
            ),
            battery=Battery(remaining_percent=75.0),
            flight_mode=FlightMode.MISSION,
            is_armed=True,
        )

        assert frame.timestamp_s == 1000.0
        assert frame.position is not None
        assert frame.battery is not None
        assert frame.flight_mode == FlightMode.MISSION
        assert frame.is_armed is True

    def test_to_vehicle_state(self) -> None:
        """Test converting frame to VehicleState."""
        frame = TelemetryFrame(
            timestamp_s=1000.0,
            position=Position(
                latitude_deg=47.0,
                longitude_deg=8.0,
                absolute_altitude_m=450.0,
                relative_altitude_m=10.0,
            ),
            battery=Battery(remaining_percent=50.0),
            flight_mode=FlightMode.HOLD,
            is_armed=True,
            connection_state=ConnectionState.CONNECTED,
        )

        state = frame.to_vehicle_state()

        assert isinstance(state, VehicleState)
        assert state.position is not None
        assert state.position.latitude_deg == 47.0
        assert state.battery is not None
        assert state.battery.remaining_percent == 50.0
        assert state.flight_mode == FlightMode.HOLD
        assert state.is_armed is True


class TestTelemetryRecorder:
    """Tests for TelemetryRecorder."""

    def test_record_frames(self) -> None:
        """Test recording frames."""
        recorder = TelemetryRecorder()

        state = VehicleState(
            position=Position(
                latitude_deg=47.0,
                longitude_deg=8.0,
                absolute_altitude_m=450.0,
                relative_altitude_m=10.0,
            ),
            battery=Battery(remaining_percent=80.0),
            flight_mode=FlightMode.MISSION,
            is_armed=True,
            connection_state=ConnectionState.CONNECTED,
        )

        recorder.record(state)
        recorder.record(state)

        assert len(recorder.frames) == 2

    def test_save_and_load(self) -> None:
        """Test saving and loading frames."""
        recorder = TelemetryRecorder()

        state = VehicleState(
            position=Position(
                latitude_deg=47.398,
                longitude_deg=8.546,
                absolute_altitude_m=460.0,
                relative_altitude_m=20.0,
            ),
            battery=Battery(remaining_percent=75.0, voltage_v=12.4),
            health=Health(is_armable=True),
            flight_mode=FlightMode.MISSION,
            is_armed=True,
            connection_state=ConnectionState.CONNECTED,
        )

        recorder.record(state)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            temp_path = Path(f.name)

        recorder.save(temp_path)

        player = TelemetryPlayer.from_file(temp_path)
        assert player.total_frames == 1

        loaded_frame = player.next_frame()
        assert loaded_frame is not None
        assert loaded_frame.position is not None
        assert loaded_frame.position.latitude_deg == 47.398
        assert loaded_frame.battery is not None
        assert loaded_frame.battery.remaining_percent == 75.0

        temp_path.unlink()


class TestTelemetryPlayer:
    """Tests for TelemetryPlayer."""

    @pytest.fixture
    def sample_frames(self) -> list[TelemetryFrame]:
        """Create sample frames."""
        base_time = time.time()
        return [
            TelemetryFrame(
                timestamp_s=base_time,
                position=Position(
                    latitude_deg=47.0,
                    longitude_deg=8.0,
                    absolute_altitude_m=450.0,
                    relative_altitude_m=10.0,
                ),
                battery=Battery(remaining_percent=90.0),
                flight_mode=FlightMode.TAKEOFF,
                is_armed=True,
            ),
            TelemetryFrame(
                timestamp_s=base_time + 0.1,
                position=Position(
                    latitude_deg=47.001,
                    longitude_deg=8.001,
                    absolute_altitude_m=460.0,
                    relative_altitude_m=20.0,
                ),
                battery=Battery(remaining_percent=89.0),
                flight_mode=FlightMode.MISSION,
                is_armed=True,
            ),
            TelemetryFrame(
                timestamp_s=base_time + 0.2,
                position=Position(
                    latitude_deg=47.002,
                    longitude_deg=8.002,
                    absolute_altitude_m=470.0,
                    relative_altitude_m=30.0,
                ),
                battery=Battery(remaining_percent=88.0),
                flight_mode=FlightMode.MISSION,
                is_armed=True,
            ),
        ]

    def test_player_from_frames(self, sample_frames: list[TelemetryFrame]) -> None:
        """Test creating player from frames."""
        player = TelemetryPlayer.from_frames(sample_frames)
        assert player.total_frames == 3
        assert player.current_index == 0
        assert player.is_finished is False

    def test_next_frame(self, sample_frames: list[TelemetryFrame]) -> None:
        """Test getting next frame."""
        player = TelemetryPlayer.from_frames(sample_frames)

        frame1 = player.next_frame()
        assert frame1 is not None
        assert frame1.flight_mode == FlightMode.TAKEOFF

        frame2 = player.next_frame()
        assert frame2 is not None
        assert frame2.flight_mode == FlightMode.MISSION

        frame3 = player.next_frame()
        assert frame3 is not None

        frame4 = player.next_frame()
        assert frame4 is None
        assert player.is_finished is True

    def test_next_state(self, sample_frames: list[TelemetryFrame]) -> None:
        """Test getting next state."""
        player = TelemetryPlayer.from_frames(sample_frames)

        state = player.next_state()
        assert state is not None
        assert isinstance(state, VehicleState)
        assert state.flight_mode == FlightMode.TAKEOFF

    def test_reset(self, sample_frames: list[TelemetryFrame]) -> None:
        """Test resetting player."""
        player = TelemetryPlayer.from_frames(sample_frames)

        player.next_frame()
        player.next_frame()
        assert player.current_index == 2

        player.reset()
        assert player.current_index == 0
        assert player.is_finished is False


class TestCreateTestScenario:
    """Tests for create_test_scenario."""

    def test_basic_scenario(self) -> None:
        """Test creating a basic scenario."""
        frames = create_test_scenario("normal", duration_s=1.0, tick_hz=10.0)
        assert len(frames) == 10

        for frame in frames:
            assert frame.position is not None
            assert frame.battery is not None
            assert frame.flight_mode == FlightMode.MISSION
            assert frame.is_armed is True

    def test_geofence_breach_scenario(self) -> None:
        """Test geofence breach scenario."""
        frames = create_test_scenario(
            "geofence_breach",
            duration_s=1.0,
            tick_hz=10.0,
            start_lat=47.398,
            start_lon=8.546,
        )

        first_pos = frames[0].position
        last_pos = frames[-1].position
        assert first_pos is not None
        assert last_pos is not None
        assert last_pos.latitude_deg > first_pos.latitude_deg

    def test_battery_drain_scenario(self) -> None:
        """Test battery drain scenario."""
        frames = create_test_scenario(
            "battery_drain",
            duration_s=1.0,
            tick_hz=10.0,
            start_battery=80.0,
        )

        first_bat = frames[0].battery
        last_bat = frames[-1].battery
        assert first_bat is not None
        assert last_bat is not None
        assert last_bat.remaining_percent < first_bat.remaining_percent

    def test_gps_spike_scenario(self) -> None:
        """Test GPS spike scenario."""
        frames = create_test_scenario(
            "gps_spike",
            duration_s=1.0,
            tick_hz=10.0,
            start_lat=47.398,
        )

        spike_frame = frames[5]
        normal_frame = frames[4]
        assert spike_frame.position is not None
        assert normal_frame.position is not None
        assert spike_frame.position.latitude_deg > normal_frame.position.latitude_deg + 0.005


class TestReplayIntegration:
    """Integration tests using replay with rule engine."""

    def test_replay_with_engine(self) -> None:
        """Test replaying through rule engine."""
        from failsafe_monitor.engine import RuleEngine
        from failsafe_monitor.rules import BatteryMinRule, RuleConfig, RuleDefaults

        config = RuleConfig(
            defaults=RuleDefaults(debounce_s=0.0),
            rules=[
                BatteryMinRule(id="BAT_LOW", threshold_percent=60.0, on_violation="rtl"),
            ],
        )
        engine = RuleEngine(config)

        frames = create_test_scenario(
            "battery_drain",
            duration_s=1.0,
            tick_hz=10.0,
            start_battery=80.0,
        )

        player = TelemetryPlayer.from_frames(frames)
        all_violations = []

        while not player.is_finished:
            state = player.next_state()
            if state:
                violations = engine.tick(state)
                all_violations.extend(violations)

        assert len(all_violations) > 0
        assert any(v.rule_id == "BAT_LOW" for v in all_violations)

    def test_gps_spike_no_false_positive(self) -> None:
        """Test that GPS spike doesn't trigger violation with debounce."""
        from failsafe_monitor.engine import RuleEngine
        from failsafe_monitor.rules import GeofencePolygonRule, RuleConfig, RuleDefaults

        config = RuleConfig(
            defaults=RuleDefaults(debounce_s=0.3),
            rules=[
                GeofencePolygonRule(
                    id="GEO",
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
        engine = RuleEngine(config)

        frames = create_test_scenario(
            "gps_spike",
            duration_s=1.0,
            tick_hz=10.0,
            start_lat=47.398,
            start_lon=8.546,
        )

        player = TelemetryPlayer.from_frames(frames)
        all_violations = []

        while not player.is_finished:
            state = player.next_state()
            if state:
                violations = engine.tick(state)
                all_violations.extend(violations)

        confirmed_violations = [v for v in all_violations if v.debounce_start_s is not None]
        assert len(confirmed_violations) == 0
