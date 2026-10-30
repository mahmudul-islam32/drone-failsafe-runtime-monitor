"""
Built-in simulation scenarios.

Each scenario maps to a test case in SAFETY_RULES.md section 4. Waypoints are
(north_m, east_m, relative_alt_m) relative to home, which is the centre of the
geofence in rules/example.yaml (about 111 m x 151 m, 10 m warning buffer).
"""

from __future__ import annotations

from .simulator import Faults, Scenario

SURVEY = [
    (0.0, 0.0, 25.0),
    (30.0, -45.0, 25.0),
    (30.0, 45.0, 25.0),
    (-30.0, 45.0, 25.0),
    (-30.0, -45.0, 25.0),
    (0.0, 0.0, 25.0),
]

SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in [
        Scenario(
            id="T-00",
            name="Nominal survey",
            description="Lawn-mower survey well inside the fence, then land at home.",
            expected="No warnings, no actions",
            waypoints=SURVEY,
            duration_s=90.0,
        ),
        Scenario(
            id="T-01",
            name="Geofence breach",
            description="A bad waypoint sends the drone 35 m east of the fence. "
            "After re-entering during RTL the monitor stays latched (T-09).",
            expected="Warning near edge, RTL after 1 s debounce outside the fence",
            waypoints=[(0.0, 0.0, 25.0), (20.0, 30.0, 25.0), (20.0, 110.0, 25.0), (0.0, 0.0, 25.0)],
            duration_s=90.0,
        ),
        Scenario(
            id="T-02",
            name="Near-boundary flight",
            description="Flies parallel to the north edge, 5 m inside the fence.",
            expected="Warnings only, no action",
            waypoints=[(0.0, 0.0, 25.0), (50.5, -60.0, 25.0), (50.5, 60.0, 25.0), (0.0, 0.0, 25.0)],
            duration_s=90.0,
        ),
        Scenario(
            id="T-03",
            name="Altitude ceiling",
            description="The mission climbs to 80 m, above the 60 m ceiling.",
            expected="Warning at 55 m, RTL once above 60 m",
            waypoints=[(0.0, 0.0, 20.0), (-20.0, -30.0, 20.0), (-20.0, -30.0, 80.0), (0.0, 0.0, 80.0)],
            duration_s=120.0,
            playback_speed=4.0,
        ),
        Scenario(
            id="T-04",
            name="Low battery -> critical battery",
            description="Accelerated drain from 45 %. RTL at 25 %, then Land overrides "
            "RTL at 12 % on the way home (T-05).",
            expected="RTL at 25 %, LAND at 12 %",
            waypoints=[(0.0, 0.0, 30.0), (-45.0, -65.0, 30.0), (40.0, -65.0, 30.0), (40.0, 65.0, 30.0)],
            duration_s=120.0,
            start_battery_pct=45.0,
            faults=Faults(battery_drain_pct_per_s=0.75),
        ),
        Scenario(
            id="T-06",
            name="Stale position telemetry",
            description="The position stream freezes for 6 s mid-mission.",
            expected="RTL once position is older than 2 s (+1 s debounce)",
            waypoints=SURVEY,
            duration_s=90.0,
            faults=Faults(position_freeze_s=(20.0, 26.0)),
        ),
        Scenario(
            id="T-07",
            name="MAVLink link loss",
            description="The link drops for 15 s. The monitor is blind and cannot act; "
            "PX4's own data-link-loss failsafe (5 s) is the backstop.",
            expected="BLIND logged, PX4 engages RTL, monitor sees RTL after link returns",
            waypoints=SURVEY,
            duration_s=100.0,
            faults=Faults(link_loss_s=(22.0, 37.0)),
        ),
        Scenario(
            id="T-08",
            name="Single-sample GPS spike",
            description="One GPS sample jumps 60 m north, outside the fence.",
            expected="Debounce filters it: zero actions",
            waypoints=SURVEY,
            duration_s=90.0,
            faults=Faults(gps_spike_at_s=20.0, gps_spike_offset_m=(60.0, 0.0)),
        ),
        Scenario(
            id="T-10",
            name="RTL not confirmed",
            description="Geofence breach, but the autopilot ignores the RTL command.",
            expected="RTL retried, then escalated to LAND",
            waypoints=[(0.0, 0.0, 25.0), (-20.0, -30.0, 25.0), (-20.0, -110.0, 25.0)],
            duration_s=90.0,
            faults=Faults(ignore_rtl=True),
        ),
    ]
}


def get_scenario(scenario_id: str) -> Scenario:
    """Look up a scenario by ID (case-insensitive)."""
    key = scenario_id.upper()
    if key not in SCENARIOS:
        raise KeyError(f"Unknown scenario {scenario_id!r}. Available: {', '.join(SCENARIOS)}")
    return SCENARIOS[key]
