"""
Closed-loop software simulator for the failsafe monitor.

Runs the real rule engine and action executor against a lightweight kinematic
multicopter model, so the full loop (telemetry -> rules -> action -> vehicle)
can be exercised and visualised without PX4 SITL or Gazebo.

The vehicle model is intentionally simple: it follows waypoints with speed and
climb-rate limits, drains its battery, and reacts to RTL / Land commands roughly
like PX4 does. Fault injection covers the scenarios in SAFETY_RULES.md section 4.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .engine import RuleEngine
from .executor import ActionExecutor, ExecutorConfig
from .logging import Event, EventLogger, EventType
from .models import (
    Battery,
    ConnectionState,
    FlightMode,
    Health,
    MonitorState,
    Position,
    Severity,
    VehicleState,
)
from .rules import AltitudeMaxRule, BatteryMinRule, GeofencePolygonRule, RuleConfig

logger = logging.getLogger(__name__)

EARTH_RADIUS_M = 6378137.0

# Fixed epoch so simulated logs are reproducible (2026-01-01T00:00:00Z).
SIM_EPOCH_S = 1767225600.0


class LocalFrame:
    """Flat-earth conversion between lat/lon and local north/east metres."""

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0 = lat0
        self.lon0 = lon0
        self._cos_lat0 = math.cos(math.radians(lat0))

    def to_local(self, lat: float, lon: float) -> tuple[float, float]:
        """Return (north_m, east_m) for a lat/lon."""
        north = math.radians(lat - self.lat0) * EARTH_RADIUS_M
        east = math.radians(lon - self.lon0) * EARTH_RADIUS_M * self._cos_lat0
        return north, east

    def to_global(self, north: float, east: float) -> tuple[float, float]:
        """Return (lat, lon) for local north/east metres."""
        lat = self.lat0 + math.degrees(north / EARTH_RADIUS_M)
        lon = self.lon0 + math.degrees(east / (EARTH_RADIUS_M * self._cos_lat0))
        return lat, lon


@dataclass
class Faults:
    """Fault injection for a simulated flight. Times are seconds since start."""

    gps_spike_at_s: float | None = None
    gps_spike_offset_m: tuple[float, float] = (60.0, 0.0)
    position_freeze_s: tuple[float, float] | None = None
    link_loss_s: tuple[float, float] | None = None
    ignore_rtl: bool = False
    battery_drain_pct_per_s: float = 0.08


@dataclass
class Scenario:
    """A scripted flight with optional fault injection."""

    id: str
    name: str
    description: str
    expected: str
    waypoints: list[tuple[float, float, float]]
    duration_s: float = 120.0
    cruise_speed_m_s: float = 6.0
    start_battery_pct: float = 95.0
    faults: Faults = field(default_factory=Faults)
    playback_speed: float = 3.0


class SimMode(str, Enum):
    """Internal vehicle phases, mapped onto PX4 flight modes."""

    IDLE = "idle"
    TAKEOFF = "takeoff"
    MISSION = "mission"
    HOLD = "hold"
    RTL = "rtl"
    LAND = "land"
    LANDED = "landed"


_MODE_MAP = {
    SimMode.IDLE: FlightMode.READY,
    SimMode.TAKEOFF: FlightMode.TAKEOFF,
    SimMode.MISSION: FlightMode.MISSION,
    SimMode.HOLD: FlightMode.HOLD,
    SimMode.RTL: FlightMode.RETURN_TO_LAUNCH,
    SimMode.LAND: FlightMode.LAND,
    SimMode.LANDED: FlightMode.READY,
}


class SimVehicle:
    """Kinematic multicopter with a PX4-like mode machine."""

    MAX_CLIMB_M_S = 3.0
    MAX_DESCENT_M_S = 1.5
    RTL_SPEED_M_S = 8.0
    RTL_MIN_ALT_M = 30.0
    DLL_TIMEOUT_S = 5.0  # PX4 COM_DL_LOSS_T: data-link-loss failsafe delay

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.north = 0.0
        self.east = 0.0
        self.alt = 0.0
        self.mode = SimMode.IDLE
        self.armed = False
        self.battery_pct = scenario.start_battery_pct
        self.wp_index = 0
        self.rtl_phase = "climb"
        self.rtl_alt = self.RTL_MIN_ALT_M
        self.mode_source = "mission"
        self.link_lost_since: float | None = None

    @property
    def flight_mode(self) -> FlightMode:
        return _MODE_MAP[self.mode]

    @property
    def voltage_v(self) -> float:
        return 10.5 + 2.1 * self.battery_pct / 100.0

    def arm_and_takeoff(self) -> None:
        self.armed = True
        self.mode = SimMode.TAKEOFF

    def command_rtl(self, source: str = "monitor") -> None:
        if self.mode in (SimMode.RTL, SimMode.LAND, SimMode.LANDED):
            return
        self.mode = SimMode.RTL
        self.mode_source = source
        self.rtl_phase = "climb"
        self.rtl_alt = max(self.alt, self.RTL_MIN_ALT_M)

    def command_land(self, source: str = "monitor") -> None:
        if self.mode in (SimMode.LAND, SimMode.LANDED):
            return
        self.mode = SimMode.LAND
        self.mode_source = source

    def step(self, t: float, dt: float, link_up: bool) -> None:
        """Advance the vehicle by dt seconds."""
        self._px4_link_failsafe(t, link_up)

        if self.mode == SimMode.TAKEOFF:
            target_alt = self.scenario.waypoints[0][2] if self.scenario.waypoints else 10.0
            self._move_towards(self.north, self.east, target_alt, 0.0, dt)
            if abs(self.alt - target_alt) < 0.3:
                self.mode = SimMode.MISSION
        elif self.mode == SimMode.MISSION:
            n, e, a = self.scenario.waypoints[self.wp_index]
            if self._move_towards(n, e, a, self.scenario.cruise_speed_m_s, dt):
                self.wp_index += 1
                if self.wp_index >= len(self.scenario.waypoints):
                    self.mode = SimMode.LAND
                    self.mode_source = "mission"
        elif self.mode == SimMode.RTL:
            self._step_rtl(dt)
        elif self.mode == SimMode.LAND:
            self._move_towards(self.north, self.east, 0.0, 0.0, dt)

        if self.mode in (SimMode.LAND, SimMode.RTL) and self.alt <= 0.05 and self.armed:
            self.alt = 0.0
            self.armed = False
            self.mode = SimMode.LANDED

        if self.armed:
            descending = self.mode == SimMode.LAND or (
                self.mode == SimMode.RTL and self.rtl_phase == "descend"
            )
            load_factor = 0.5 if descending else 1.0 + 0.15 * self.scenario.cruise_speed_m_s / 6.0
            drain = self.scenario.faults.battery_drain_pct_per_s * load_factor
            self.battery_pct = max(0.0, self.battery_pct - drain * dt)

    def _px4_link_failsafe(self, t: float, link_up: bool) -> None:
        """PX4's own data-link-loss failsafe (NAV_DLL_ACT = RTL) as the backstop."""
        if link_up:
            self.link_lost_since = None
            return
        if self.link_lost_since is None:
            self.link_lost_since = t
        if t - self.link_lost_since >= self.DLL_TIMEOUT_S and self.armed:
            self.command_rtl(source="px4")

    def _step_rtl(self, dt: float) -> None:
        if self.rtl_phase == "climb":
            if self._move_towards(self.north, self.east, self.rtl_alt, 0.0, dt):
                self.rtl_phase = "return"
        elif self.rtl_phase == "return":
            if self._move_towards(0.0, 0.0, self.rtl_alt, self.RTL_SPEED_M_S, dt):
                self.rtl_phase = "descend"
        else:
            self._move_towards(0.0, 0.0, 0.0, 0.0, dt)

    def _move_towards(
        self, n: float, e: float, a: float, speed: float, dt: float
    ) -> bool:
        """Move towards a target; return True once it is reached."""
        dn, de = n - self.north, e - self.east
        dist = math.hypot(dn, de)
        step = speed * dt
        if dist <= step or dist < 0.5:
            self.north, self.east = n, e
        else:
            self.north += dn / dist * step
            self.east += de / dist * step

        da = a - self.alt
        max_dz = (self.MAX_CLIMB_M_S if da > 0 else self.MAX_DESCENT_M_S) * dt
        self.alt += max(-max_dz, min(max_dz, da))
        return math.hypot(n - self.north, e - self.east) < 0.5 and abs(a - self.alt) < 0.3


class SimTelemetry:
    """What the monitor 'sees': vehicle state with injected telemetry faults."""

    def __init__(self, vehicle: SimVehicle, frame: LocalFrame, clock: SimClock) -> None:
        self._vehicle = vehicle
        self._frame = frame
        self._clock = clock
        self._last_position: Position | None = None
        self._last_state: VehicleState | None = None

    def link_up(self, t: float) -> bool:
        loss = self._vehicle.scenario.faults.link_loss_s
        return not (loss and loss[0] <= t < loss[1])

    def _position_frozen(self, t: float) -> bool:
        freeze = self._vehicle.scenario.faults.position_freeze_s
        return bool(freeze and freeze[0] <= t < freeze[1])

    def observed_ne(self, t: float) -> tuple[float, float]:
        """North/east as reported by GPS, including a spike if injected."""
        v = self._vehicle
        spike_t = v.scenario.faults.gps_spike_at_s
        if spike_t is not None and abs(t - spike_t) < 0.05:
            dn, de = v.scenario.faults.gps_spike_offset_m
            return v.north + dn, v.east + de
        return v.north, v.east

    @property
    def state(self) -> VehicleState:
        """Current vehicle state as seen by the monitor."""
        t = self._clock.t
        now = self._clock()
        if not self.link_up(t) and self._last_state is not None:
            frozen = self._last_state
            frozen.connection_state = ConnectionState.DISCONNECTED
            return frozen

        v = self._vehicle
        if not self._position_frozen(t) or self._last_position is None:
            n, e = self.observed_ne(t)
            lat, lon = self._frame.to_global(n, e)
            self._last_position = Position(
                latitude_deg=lat,
                longitude_deg=lon,
                absolute_altitude_m=488.0 + v.alt,
                relative_altitude_m=v.alt,
                timestamp_s=now,
            )

        state = VehicleState(
            position=self._last_position,
            battery=Battery(remaining_percent=v.battery_pct, voltage_v=v.voltage_v, timestamp_s=now),
            health=Health(timestamp_s=now),
            flight_mode=v.flight_mode,
            flight_mode_timestamp_s=now,
            is_armed=v.armed,
            armed_timestamp_s=now,
            connection_state=ConnectionState.CONNECTED,
            connection_timestamp_s=now,
        )
        self._last_state = state
        return state


class SimClock:
    """Deterministic simulation clock returning epoch seconds."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return SIM_EPOCH_S + self.t


class _SimAction:
    """Stand-in for mavsdk.System.action, wired to the simulated vehicle."""

    def __init__(self, vehicle: SimVehicle, link_up: Any) -> None:
        self._vehicle = vehicle
        self._link_up = link_up

    async def return_to_launch(self) -> None:
        if self._link_up() and not self._vehicle.scenario.faults.ignore_rtl:
            self._vehicle.command_rtl()

    async def land(self) -> None:
        if self._link_up():
            self._vehicle.command_land()


class _SimSystem:
    def __init__(self, action: _SimAction) -> None:
        self.action = action


@dataclass
class SimFrame:
    """One recorded simulation tick, used for visualisation and reports."""

    t: float
    north: float
    east: float
    alt: float
    seen_north: float | None
    seen_east: float | None
    battery_pct: float
    flight_mode: FlightMode
    armed: bool
    monitor_state: MonitorState
    link_up: bool
    position_age_s: float
    warnings: list[str]
    violations: list[str]


@dataclass
class ActionRecord:
    """A failsafe intervention and its timing."""

    rule_id: str
    action: str
    onset_t: float
    detect_t: float
    act_t: float
    confirmed: bool
    retries: int
    note: str = ""

    @property
    def time_to_detect_s(self) -> float:
        return self.detect_t - self.onset_t

    @property
    def time_to_act_s(self) -> float:
        return self.act_t - self.onset_t


@dataclass
class SimEvent:
    """A notable event with simulation-relative time, for overlays."""

    t: float
    kind: str
    message: str
    severity: str | None = None


@dataclass
class SimResult:
    """Everything recorded during one simulated flight."""

    scenario: Scenario
    config: RuleConfig
    frame: LocalFrame
    fence_local: list[tuple[float, float]]
    warn_fence_local: list[tuple[float, float]]
    frames: list[SimFrame]
    events: list[SimEvent]
    actions: list[ActionRecord]
    log_events: list[Event]
    log_file: Path | None = None

    @property
    def altitude_ceiling_m(self) -> float | None:
        for rule in self.config.rules:
            if isinstance(rule, AltitudeMaxRule) and rule.enabled:
                return rule.max_relative_alt_m
        return None

    @property
    def altitude_warn_m(self) -> float | None:
        for rule in self.config.rules:
            if isinstance(rule, AltitudeMaxRule) and rule.enabled and rule.warn_buffer_m:
                return rule.max_relative_alt_m - rule.warn_buffer_m
        return None

    @property
    def battery_thresholds(self) -> list[tuple[str, float, str]]:
        return [
            (r.id, r.threshold_percent, r.on_violation)
            for r in self.config.rules
            if isinstance(r, BatteryMinRule) and r.enabled
        ]

    @property
    def max_severity(self) -> str:
        if not self.actions:
            warned = any(e.kind == "warning" for e in self.events)
            return "warn" if warned else "none"
        return max(Severity(a.action) for a in self.actions).value

    def frame_at(self, t: float) -> SimFrame:
        idx = min(len(self.frames) - 1, max(0, round(t / (self.frames[1].t - self.frames[0].t))))
        return self.frames[idx]


def _geofence(config: RuleConfig) -> GeofencePolygonRule | None:
    for rule in config.rules:
        if isinstance(rule, GeofencePolygonRule) and rule.enabled:
            return rule
    return None


def build_frame(config: RuleConfig) -> LocalFrame:
    """Use the geofence centroid as home, or PX4's default SITL home."""
    fence = _geofence(config)
    if fence is None:
        return LocalFrame(47.397742, 8.545594)
    lats = [p[0] for p in fence.polygon]
    lons = [p[1] for p in fence.polygon]
    return LocalFrame(sum(lats) / len(lats), sum(lons) / len(lons))


async def run_scenario_async(
    scenario: Scenario,
    config: RuleConfig,
    log_dir: Path | str | None = None,
) -> SimResult:
    """Fly a scenario in closed loop with the real rule engine and executor."""
    clock = SimClock()
    frame = build_frame(config)
    vehicle = SimVehicle(scenario)
    telemetry = SimTelemetry(vehicle, frame, clock)
    engine = RuleEngine(config, clock=clock)
    executor = ActionExecutor(
        system=_SimSystem(_SimAction(vehicle, lambda: telemetry.link_up(clock.t))),  # type: ignore[arg-type]
        telemetry_subscriber=telemetry,  # type: ignore[arg-type]
        config=ExecutorConfig(confirmation_timeout_s=0.25, max_retries=1, retry_delay_s=0.05),
    )

    event_logger = EventLogger(log_dir=log_dir, session_id=scenario.id) if log_dir else None
    if event_logger:
        event_logger.log_file.unlink(missing_ok=True)
        event_logger.open()

    log_events: list[Event] = []
    sim_events: list[SimEvent] = []
    actions: list[ActionRecord] = []

    def log(event_type: EventType, message: str, **kwargs: Any) -> None:
        event = Event(event_type=event_type, timestamp_s=clock(), message=message, **kwargs)
        log_events.append(event)
        if event_logger:
            event_logger.log(event)

    def note(kind: str, message: str, severity: str | None = None) -> None:
        sim_events.append(SimEvent(t=round(clock.t, 2), kind=kind, message=message, severity=severity))

    fence = _geofence(config)
    fence_local = [frame.to_local(*p) for p in fence.polygon] if fence else []
    warn_local: list[tuple[float, float]] = []
    if fence is not None and fence.id in engine._warn_polygons:
        warn_local = [
            frame.to_local(lat, lon) for lon, lat in engine._warn_polygons[fence.id].exterior.coords
        ]

    log(EventType.STARTUP, "Monitor started", data={"scenario": scenario.id, "vehicle_id": config.vehicle_id})
    log(EventType.CONNECTED, "Connected to vehicle (simulated)")
    note("info", "Monitor connected")

    dt = 1.0 / config.tick_hz
    frames: list[SimFrame] = []
    last_state = engine.monitor_state
    last_mode = vehicle.flight_mode
    last_link = True
    warning_rules: set[str] = set()
    landed_at: float | None = None
    steps = int(scenario.duration_s * config.tick_hz)

    for i in range(steps + 1):
        clock.t = round(i * dt, 6)
        t = clock.t
        if i == int(2.0 * config.tick_hz):
            vehicle.arm_and_takeoff()
            note("info", "Armed, taking off")

        link_up = telemetry.link_up(t)
        if link_up != last_link:
            if link_up:
                log(EventType.CONNECTED, "Link restored")
                note("info", "MAVLink restored")
            else:
                log(EventType.DISCONNECTED, "Connection lost: link down", data={"reason": "link down"})
                note("alert", "MAVLink lost - monitor BLIND", "warn")
            last_link = link_up

        vehicle.step(t, dt, link_up)

        if vehicle.flight_mode != last_mode:
            src = vehicle.mode_source if vehicle.mode in (SimMode.RTL, SimMode.LAND) else ""
            label = f"Flight mode -> {vehicle.flight_mode.value.upper()}"
            if src == "px4":
                label += " (PX4 link-loss failsafe)"
            note("mode", label)
            last_mode = vehicle.flight_mode

        state = telemetry.state
        violations = engine.tick(state)

        current = engine.monitor_state
        if current != last_state:
            log(
                EventType.STATE_CHANGE,
                f"State: {last_state.value} -> {current.value}",
                data={"old_state": last_state.value, "new_state": current.value},
            )
            last_state = current

        tick_warnings: list[str] = []
        for v in violations:
            if v.severity == Severity.WARN and v.debounce_start_s is None:
                tick_warnings.append(v.rule_id)
                if v.rule_id not in warning_rules:
                    log(EventType.WARNING, v.message, data=v.values)
                    note("warning", f"{v.rule_id}: {v.message}", "warn")
                continue
            onset = (v.debounce_start_s or clock()) - SIM_EPOCH_S
            log(
                EventType.VIOLATION,
                v.message,
                data={**v.values, "onset_s": onset + SIM_EPOCH_S},
                rule_id=v.rule_id,
                severity=v.severity.value,
            )
            note("violation", f"{v.rule_id}: {v.message}", v.severity.value)
        warning_rules = set(tick_warnings)

        request = engine.get_action_request(violations)
        if request:
            onset = (request.source_violation.debounce_start_s or clock()) - SIM_EPOCH_S
            log(
                EventType.ACTION_REQUESTED,
                f"Action requested: {request.action.value}",
                data={"action": request.action.value},
                rule_id=request.source_rule_id,
                severity=request.action.value,
            )
            mode_before = vehicle.flight_mode
            result = await executor.execute(request)
            final_action = Severity.LAND if vehicle.mode == SimMode.LAND else request.action
            if result.success:
                engine.record_executed_action(final_action)
            already = mode_before == vehicle.flight_mode and result.confirmed
            if result.confirmed and final_action != request.action:
                note_txt = f"{request.action.value.upper()} not confirmed, escalated to LAND"
            elif already:
                note_txt = f"Already in {vehicle.flight_mode.value.upper()}"
            else:
                note_txt = ""
            log(
                EventType.ACTION_CONFIRMED if result.success else EventType.ACTION_FAILED,
                f"Action {'confirmed' if result.success else 'failed'}: {final_action.value}",
                data={
                    "action": final_action.value,
                    "success": result.success,
                    "latency_s": 0.0,
                    "retry_count": result.retry_count,
                    "onset_s": onset + SIM_EPOCH_S,
                    "note": note_txt,
                },
                rule_id=request.source_rule_id,
            )
            if not already:
                actions.append(
                    ActionRecord(
                        rule_id=request.source_rule_id,
                        action=final_action.value,
                        onset_t=round(onset, 2),
                        detect_t=t,
                        act_t=t,
                        confirmed=result.confirmed,
                        retries=result.retry_count,
                        note=note_txt,
                    )
                )
                msg = f"Monitor commanded {final_action.value.upper()} ({request.source_rule_id})"
                if note_txt:
                    msg += f" - {note_txt}"
                note("action", msg, final_action.value)

        seen = state.position
        seen_ne = frame.to_local(seen.latitude_deg, seen.longitude_deg) if seen else (None, None)
        rule_states = engine.state.rule_states
        frames.append(
            SimFrame(
                t=t,
                north=vehicle.north,
                east=vehicle.east,
                alt=vehicle.alt,
                seen_north=seen_ne[0],
                seen_east=seen_ne[1],
                battery_pct=vehicle.battery_pct,
                flight_mode=vehicle.flight_mode,
                armed=vehicle.armed,
                monitor_state=engine.monitor_state,
                link_up=link_up,
                position_age_s=(clock() - seen.timestamp_s) if seen else 0.0,
                warnings=sorted(warning_rules),
                violations=sorted(rid for rid, rs in rule_states.items() if rs.is_violated),
            )
        )

        if vehicle.mode == SimMode.LANDED:
            if landed_at is None:
                landed_at = t
                note("info", "Landed and disarmed")
            elif t - landed_at >= 3.0:
                break

    log(EventType.SHUTDOWN, "Monitor stopped: normal", data={"reason": "normal"})
    if event_logger:
        event_logger.close()

    return SimResult(
        scenario=scenario,
        config=config,
        frame=frame,
        fence_local=fence_local,
        warn_fence_local=warn_local,
        frames=frames,
        events=sim_events,
        actions=actions,
        log_events=log_events,
        log_file=event_logger.log_file if event_logger else None,
    )


def run_scenario(
    scenario: Scenario,
    config: RuleConfig,
    log_dir: Path | str | None = None,
) -> SimResult:
    """Synchronous wrapper around run_scenario_async."""
    return asyncio.run(run_scenario_async(scenario, config, log_dir))
