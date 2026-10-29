# Architecture: drone-failsafe-runtime-monitor

> Design document. Status: **draft, planned**.

## 1. Principles
- **Independence:** the monitor runs as a separate process with its own specification, and does not change PX4 code or parameters.
- **Simplicity:** small, reviewable rule logic, which supports an argument that the monitor is correct.
- **Defence in depth:** PX4's own failsafes (geofence, low battery, data-link loss) stay enabled. The monitor is an additional layer.
- **Fail-safe defaults:** escalate, never de-escalate automatically, during a flight.

## 2. Components

```mermaid
flowchart TB
    CONN[Connection manager<br/>MAVSDK System] --> SUB[Telemetry subscribers<br/>asyncio tasks]
    SUB --> STATE[Vehicle state snapshot<br/>with timestamps]
    STATE --> ENG[Rule engine tick loop]
    CFG[Rule loader and validator] --> ENG
    ENG --> DEC[Decision: none / warn / rtl / land]
    DEC --> EXE[Action executor]
    EXE --> CONN
    DEC --> LOG[Event logger]
    ENG -. optional .-> ROS[ROS 2 bridge]
```

## 3. Monitor state machine

```mermaid
stateDiagram-v2
    [*] --> Connecting
    Connecting --> Monitoring: connected and telemetry fresh
    Monitoring --> Warning: warn-level violation
    Warning --> Monitoring: cleared (hysteresis)
    Monitoring --> RTL: rtl-level violation persisted
    Warning --> RTL: rtl-level violation persisted
    RTL --> Land: land-level violation or RTL not confirmed
    Monitoring --> Land: land-level violation
    RTL --> Done: disarmed
    Land --> Done: disarmed
    Monitoring --> Blind: telemetry stale or link lost
    Blind --> Monitoring: telemetry restored
    Done --> [*]
```

In `Blind` the monitor cannot command the vehicle. It logs the condition, and PX4's data-link-loss failsafe is expected to act (see SR-07).

## 4. Intervention sequence

```mermaid
sequenceDiagram
    participant PX4 as PX4 SITL
    participant T as Telemetry task
    participant E as Rule engine
    participant A as Action executor
    PX4-->>T: position stream
    T->>E: snapshot update
    E->>E: geofence violated for at least debounce_s
    E->>A: request RTL
    A->>PX4: action.return_to_launch()
    PX4-->>T: flight_mode = RETURN_TO_LAUNCH
    T->>A: mode confirmed
    A->>E: action confirmed, latency logged
```

## 5. Timing budget (planned targets in SITL)
| Stage | Target |
|---|---|
| Telemetry age at evaluation | ≤ 200 ms |
| Rule tick | 10 Hz |
| Detection after violation onset | debounce_s + ≤ 100 ms |
| Command to mode confirmation | ≤ 2 s, otherwise retry and escalate |

## 6. Test architecture
- **Unit:** pure rule functions with synthetic snapshots.
- **Replay:** recorded JSONL telemetry fed into the engine (no simulator).
- **SITL integration:** headless PX4 + Gazebo, scripted with MAVSDK (takeoff, offboard or mission legs), with faults induced through flight paths, PX4 simulation parameters (e.g. battery drain) or network blocking.
