# Safety Rules, Requirements and Test Scenarios

> Status: **draft, planned**. Thresholds are example values for simulation only.

## 1. Scope and assumptions
- Vehicle: PX4 multicopter (e.g. `gz_x500`) in SITL with Gazebo.
- The monitor connects over MAVLink with MAVSDK-Python. PX4 built-in failsafes remain enabled as a backstop.
- Positions are WGS84 lat/lon. Altitude is relative to home (`relative_altitude_m`).
- Battery percentage comes from the MAVSDK battery telemetry `remaining_percent`. The value range (0–1 vs 0–100) has to be confirmed for the pinned MAVSDK version, and the loader will normalise it.

## 2. Rule specification format (YAML, planned schema v1)

```yaml
version: 1
vehicle_id: sitl-x500
tick_hz: 10
defaults:
  debounce_s: 1.0
  hysteresis: 0.05          # fraction of the threshold, for clearing warnings
rules:
  - id: GEOFENCE_POLYGON
    type: geofence_polygon
    polygon:                # lat, lon in degrees (closed automatically)
      - [47.397742, 8.545594]
      - [47.398742, 8.545594]
      - [47.398742, 8.547594]
      - [47.397742, 8.547594]
    warn_buffer_m: 10
    on_warn: warn
    on_violation: rtl
  - id: ALT_CEILING
    type: altitude_max
    max_relative_alt_m: 60
    on_violation: rtl
  - id: BATTERY_RTL
    type: battery_min
    threshold_percent: 25
    on_violation: rtl
  - id: BATTERY_LAND
    type: battery_min
    threshold_percent: 12
    on_violation: land
  - id: TELEMETRY_STALE
    type: telemetry_max_age
    stream: position
    max_age_s: 2.0
    on_violation: rtl       # attempted only if the link is still up
  - id: LINK_LOST
    type: connection_lost
    on_violation: log_only  # monitor is blind; PX4 data-link-loss failsafe is the backstop
```

The default polygon is placed near PX4's default SITL home location (around Zurich). It must be adjusted if the home location differs.

**Severity order:** `log_only` < `warn` < `rtl` < `land`. When several rules fire, the highest severity wins.

## 3. Safety requirements

| ID | Requirement | Rule(s) | Response | Verification |
|---|---|---|---|---|
| SR-01 | If the vehicle is outside the inclusion polygon for ≥ debounce_s, the monitor shall command RTL. | GEOFENCE_POLYGON | RTL | T-01 |
| SR-02 | If the vehicle is within `warn_buffer_m` of the boundary, the monitor shall raise a warning without commanding. | GEOFENCE_POLYGON | Warn | T-02 |
| SR-03 | If the relative altitude exceeds the ceiling for ≥ debounce_s, the monitor shall command RTL. | ALT_CEILING | RTL | T-03 |
| SR-04 | If the battery drops below the RTL threshold, the monitor shall command RTL. | BATTERY_RTL | RTL | T-04 |
| SR-05 | If the battery drops below the Land threshold, the monitor shall command Land, overriding RTL. | BATTERY_LAND | Land | T-05 |
| SR-06 | If position telemetry is older than max_age_s while connected, the monitor shall command RTL. | TELEMETRY_STALE | RTL | T-06 |
| SR-07 | If the MAVLink connection is lost, the monitor shall log a BLIND event, and the test shall confirm that PX4's own data-link-loss failsafe engages. | LINK_LOST | Log + PX4 backstop | T-07 |
| SR-08 | A violation shorter than debounce_s shall not trigger an action. | all | None | T-08 |
| SR-09 | Once RTL or Land is commanded, the monitor shall not de-escalate during the same flight. | engine | Latch | T-09 |
| SR-10 | If a commanded mode is not confirmed within 2 s, the monitor shall retry once, then command Land. | executor | Escalate | T-10 |
| SR-11 | An invalid rule file shall prevent the monitor from starting. | loader | Abort | T-11 |

## 4. Test scenarios (planned, SITL unless noted)

| ID | Scenario | Setup / fault injection | Expected result | Pass criteria |
|---|---|---|---|---|
| T-01 | Geofence breach | Offboard or goto command to a point 30 m outside the polygon | RTL commanded | Mode = RTL within debounce + 2 s of crossing |
| T-02 | Near-boundary flight | Fly parallel 5 m inside the boundary | Warning only | Warning events, no action |
| T-03 | Altitude ceiling | Climb to 80 m | RTL | As T-01 |
| T-04 | Low battery | Speed up simulated battery drain (PX4 SITL battery simulation parameters, e.g. `SIM_BAT_DRAIN`) | RTL at 25 % | Action logged at the threshold ±2 % |
| T-05 | Critical battery during RTL | Continue the drain after T-04 | Land overrides RTL | Mode = LAND |
| T-06 | Stale telemetry | Replay test: freeze position stream timestamps | RTL decision | Decision within max_age_s + 1 tick |
| T-07 | Link loss | Block the MAVLink UDP port (e.g. firewall rule) | BLIND logged. PX4 failsafe (per `NAV_DLL_ACT` setting) engages | BLIND event plus PX4 mode change observed after the link is restored |
| T-08 | GPS spike | Replay test: single-sample position jump outside the fence | No action | Zero actions |
| T-09 | Re-entry after RTL | Vehicle returns inside the fence during RTL | Stays in RTL | No de-escalation command |
| T-10 | Command not confirmed | Mock the executor to ignore RTL | Retry, then Land | Land within 2 × timeout |
| T-11 | Bad rule file | Missing `threshold_percent` | Startup aborted | Non-zero exit and clear error |

## 5. Hazard notes
- **False positives** (unnecessary RTL) are accepted in favour of **false negatives** in this simulation context.
- **Conflicting failsafes:** PX4 and the monitor may both act. The test log records which one acted first.
- **Not covered:** obstacle avoidance, multi-vehicle operation, real RF link behaviour, certification (e.g. DO-178C) aspects.
