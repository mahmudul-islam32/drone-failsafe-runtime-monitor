# Feature Specification: drone-failsafe-runtime-monitor

All features are **Planned**. Priority: Must, Should, Could. Requirement IDs (SR-xx) refer to [SAFETY_RULES.md](SAFETY_RULES.md).

| ID | Feature | Priority | Status |
|---|---|---|---|
| F-01 | MAVSDK connection and telemetry subscriber | Must | Planned |
| F-02 | YAML rule file with schema validation | Must | Planned |
| F-03 | Geofence rule (polygon and altitude) | Must | Planned |
| F-04 | Battery rules | Must | Planned |
| F-05 | Telemetry-staleness / link watchdog | Must | Planned |
| F-06 | Rule engine with debounce, hysteresis and escalation | Must | Planned |
| F-07 | Action executor with confirmation | Must | Planned |
| F-08 | Dry-run mode | Should | Planned |
| F-09 | Event log and after-flight report | Should | Implemented |
| F-10 | Automated SITL scenario suite | Must | Planned |
| F-11 | Recorded-telemetry replay for unit tests | Should | Planned |
| F-12 | ROS 2 status bridge | Could | Planned |
| F-13 | Closed-loop simulator, dashboard video and screenshots | Should | Implemented |

---

### F-01: MAVSDK connection and telemetry subscriber
**Description:** Connects to PX4 SITL with `System().connect(...)`, waits for `core.connection_state()` to report connected, and subscribes to the `telemetry.position()`, `battery()`, `flight_mode()`, `armed()` and `health()` streams as asyncio tasks.
**User story:** As a developer, I want a single, timestamped vehicle state snapshot that the rules can read.
**Acceptance criteria:** The snapshot updates at ≥ 5 Hz in SITL. Each field carries a receive timestamp. Losing the connection is detected and logged.
**Priority:** Must · **Status:** Planned

### F-02: YAML rule file with schema validation
**Description:** Rules are declared in YAML (format in SAFETY_RULES.md §2) and validated with Pydantic at startup.
**User story:** As a safety engineer, I want to review and change limits without touching code.
**Acceptance criteria:** An invalid file stops startup with a clear error. Unknown keys are rejected. The rule-file hash is logged.
**Priority:** Must · **Status:** Planned

### F-03: Geofence rule (polygon and altitude)
**Description:** An inclusion polygon (lat/lon) plus max/min relative altitude, with an optional warning buffer in metres.
**User story:** As an operator, I want the drone returned before it leaves the permitted area.
**Acceptance criteria:** Covers SR-01 to SR-03. Scenarios T-01 to T-03 pass.
**Priority:** Must · **Status:** Planned

### F-04: Battery rules
**Description:** Warn, RTL and Land thresholds on `battery.remaining_percent`, with an optional voltage floor.
**User story:** As an operator, I want the drone home before the battery is critical.
**Acceptance criteria:** Covers SR-04 and SR-05. Scenarios T-04 and T-05 pass. Threshold units are documented for the MAVSDK version used.
**Priority:** Must · **Status:** Planned

### F-05: Telemetry-staleness / link watchdog
**Description:** Flags a violation when no position update has arrived for `max_age_s`, or when the MAVSDK connection drops.
**User story:** As a safety engineer, I want the monitor to know when it is blind.
**Acceptance criteria:** Covers SR-06 and SR-07. If the link is lost, the monitor logs that it cannot act and relies on PX4's configured data-link-loss failsafe (documented backstop). Scenario T-06 passes.
**Priority:** Must · **Status:** Planned

### F-06: Rule engine with debounce, hysteresis and escalation
**Description:** Evaluates all rules at `tick_hz`. A violation must persist for `debounce_s`. Severity escalates warn → rtl → land and never de-escalates automatically during a flight.
**User story:** As a pilot, I don't want noisy GPS to trigger false RTLs.
**Acceptance criteria:** Covers SR-08 and SR-09. Unit tests show no trigger for a single-sample spike and a trigger after persistence.
**Priority:** Must · **Status:** Planned

### F-07: Action executor with confirmation
**Description:** Calls `action.return_to_launch()` or `action.land()`, confirms the flight-mode change via telemetry within a timeout, retries, then escalates to Land.
**User story:** As a safety engineer, I want evidence that the command took effect.
**Acceptance criteria:** Covers SR-10. Actions are idempotent, and the result is logged with latency.
**Priority:** Must · **Status:** Planned

### F-08: Dry-run mode
**Description:** Evaluates rules and logs the actions it *would* take, without sending commands.
**User story:** As a developer, I want to tune thresholds safely.
**Acceptance criteria:** No MAVSDK action call is made when `dry_run: true`, verified by a mock test.
**Priority:** Should · **Status:** Planned

### F-09: Event log and after-flight report
**Description:** JSONL events (rule, values, severity, action, latency), plus a Markdown or HTML summary per flight.
**User story:** As a reviewer, I want to see exactly why and when the monitor intervened.
**Acceptance criteria:** Every violation and action has an event, and the report lists time-to-detect and time-to-act.
**Priority:** Should · **Status:** Implemented (`failsafe_monitor/report.py`; written automatically when a session ends)

### F-10: Automated SITL scenario suite
**Description:** pytest-driven scenarios that start a flight in SITL, induce a violation and assert the monitor's response (SAFETY_RULES.md §4).
**User story:** As a developer, I want regression tests for every safety requirement.
**Acceptance criteria:** Each SR maps to at least one scenario. The suite runs headless.
**Priority:** Must · **Status:** Planned

### F-11: Recorded-telemetry replay for unit tests
**Description:** Replays recorded state snapshots into the rule engine without a simulator.
**User story:** As a developer, I want fast tests in CI without Gazebo.
**Acceptance criteria:** Replay tests run in < 30 s on a CI runner.
**Priority:** Should · **Status:** Planned

### F-12: ROS 2 status bridge
**Description:** Publishes monitor status and violations on ROS 2 topics (e.g. `std_msgs/String` JSON, or custom messages later).
**User story:** As a ROS user, I want to see monitor state in RViz or `ros2 topic echo`.
**Acceptance criteria:** Optional dependency. The monitor runs unchanged without ROS 2.
**Priority:** Could · **Status:** Planned

### F-13: Closed-loop simulator, dashboard video and screenshots
**Description:** `failsafe-sim` flies scripted scenarios (T-00 to T-10) against the real rule engine and action executor. It uses a kinematic drone model with PX4-like RTL, Land and data-link-loss behaviour, and fault injection (GPS spike, frozen telemetry, link loss, ignored RTL, fast battery drain). It writes JSONL logs, after-flight reports, dashboard PNGs, per-scenario MP4s and a combined demo video. See [SIMULATION.md](SIMULATION.md).
**User story:** As a developer or reviewer, I want to see and regression-test the monitor's behaviour in seconds, without setting up PX4 and Gazebo.
**Acceptance criteria:** Deterministic runs. Every scenario has a closed-loop test asserting the expected action. Video rendering needs only matplotlib and ffmpeg.
**Priority:** Should · **Status:** Implemented

