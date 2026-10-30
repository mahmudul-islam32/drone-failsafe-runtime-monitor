"""
Closed-loop simulation tests.

Each built-in scenario is flown against the real RuleEngine and ActionExecutor,
and the monitor's response is checked against SAFETY_RULES.md section 4.
"""

from pathlib import Path

import pytest

from failsafe_monitor.loader import load_rules
from failsafe_monitor.logging import EventType, load_events
from failsafe_monitor.models import FlightMode, MonitorState
from failsafe_monitor.report import build_report, to_html, to_markdown, write_report
from failsafe_monitor.rules import RuleConfig
from failsafe_monitor.scenarios import SCENARIOS, get_scenario
from failsafe_monitor.sim_cli import main as sim_main
from failsafe_monitor.simulator import LocalFrame, SimResult, run_scenario

RULES = Path(__file__).parent.parent / "rules" / "example.yaml"


@pytest.fixture(scope="module")
def config() -> RuleConfig:
    return load_rules(RULES)


@pytest.fixture(scope="module")
def results(config: RuleConfig) -> dict[str, SimResult]:
    return {sid: run_scenario(s, config) for sid, s in SCENARIOS.items()}


def actions(result: SimResult) -> list[tuple[str, str]]:
    return [(a.action, a.rule_id) for a in result.actions]


def test_local_frame_round_trip() -> None:
    frame = LocalFrame(47.398242, 8.546594)
    lat, lon = frame.to_global(30.0, -45.0)
    north, east = frame.to_local(lat, lon)
    assert north == pytest.approx(30.0, abs=1e-6)
    assert east == pytest.approx(-45.0, abs=1e-6)


def test_get_scenario_case_insensitive() -> None:
    assert get_scenario("t-01").id == "T-01"
    with pytest.raises(KeyError):
        get_scenario("T-99")


def test_every_flight_lands(results: dict[str, SimResult]) -> None:
    for sid, result in results.items():
        final = result.frames[-1]
        assert not final.armed, f"{sid} should end landed and disarmed"
        assert final.alt == pytest.approx(0.0, abs=0.1)


def test_simulation_is_deterministic(config: RuleConfig, results: dict[str, SimResult]) -> None:
    again = run_scenario(SCENARIOS["T-01"], config)
    assert [(f.t, f.north, f.east) for f in again.frames] == [
        (f.t, f.north, f.east) for f in results["T-01"].frames
    ]


def test_t00_nominal_has_no_actions(results: dict[str, SimResult]) -> None:
    r = results["T-00"]
    assert r.actions == []
    assert all(f.monitor_state == MonitorState.MONITORING for f in r.frames[1:])


def test_t01_geofence_breach_triggers_rtl_and_stays_latched(results: dict[str, SimResult]) -> None:
    r = results["T-01"]
    assert actions(r) == [("rtl", "GEOFENCE_POLYGON")]
    act = r.actions[0]
    assert act.time_to_detect_s == pytest.approx(1.0, abs=0.11)
    # T-09: after re-entering the fence the monitor stays in RTL until disarm.
    after = [f for f in r.frames if f.t > act.act_t and f.armed]
    assert after and all(f.monitor_state == MonitorState.RTL for f in after)
    assert all(f.flight_mode == FlightMode.RETURN_TO_LAUNCH for f in after[2:])


def test_t01_debounce_window_shows_warning(results: dict[str, SimResult]) -> None:
    r = results["T-01"]
    act = r.actions[0]
    debounce = [f for f in r.frames if act.onset_t <= f.t < act.act_t]
    assert debounce and all(f.monitor_state == MonitorState.WARNING for f in debounce)


def test_t02_near_boundary_warns_only(results: dict[str, SimResult]) -> None:
    r = results["T-02"]
    assert r.actions == []
    assert any(e.kind == "warning" for e in r.events)


def test_t03_altitude_ceiling_triggers_rtl(results: dict[str, SimResult]) -> None:
    r = results["T-03"]
    assert actions(r) == [("rtl", "ALT_CEILING")]
    assert max(f.alt for f in r.frames) < 66.0


def test_t04_t05_battery_rtl_then_land(results: dict[str, SimResult]) -> None:
    r = results["T-04"]
    assert actions(r) == [("rtl", "BATTERY_RTL"), ("land", "BATTERY_LAND")]
    rtl_frame = r.frame_at(r.actions[0].act_t)
    land_frame = r.frame_at(r.actions[1].act_t)
    assert 23.0 <= rtl_frame.battery_pct < 25.0
    assert 10.0 <= land_frame.battery_pct < 12.0
    assert r.frames[-1].battery_pct > 0.0


def test_t06_stale_telemetry_triggers_rtl(results: dict[str, SimResult]) -> None:
    r = results["T-06"]
    assert actions(r) == [("rtl", "TELEMETRY_STALE")]
    freeze_start = r.scenario.faults.position_freeze_s[0]  # type: ignore[index]
    # max_age_s (2 s) + debounce (1 s) + one tick
    assert r.actions[0].act_t - freeze_start <= 3.0 + 0.11


def test_t07_link_loss_is_blind_and_px4_takes_over(results: dict[str, SimResult]) -> None:
    r = results["T-07"]
    assert r.actions == []
    lost, restored = r.scenario.faults.link_loss_s  # type: ignore[misc]
    blind = [f for f in r.frames if lost <= f.t < restored]
    assert all(f.monitor_state == MonitorState.BLIND for f in blind)
    assert any("PX4 link-loss failsafe" in e.message for e in r.events)
    assert r.frame_at(restored + 0.5).flight_mode == FlightMode.RETURN_TO_LAUNCH


def test_t08_gps_spike_is_debounced(results: dict[str, SimResult]) -> None:
    r = results["T-08"]
    assert r.actions == []
    spike = r.frame_at(r.scenario.faults.gps_spike_at_s)  # type: ignore[arg-type]
    assert spike.seen_north is not None and spike.seen_north - spike.north > 50


def test_t10_unconfirmed_rtl_escalates_to_land(results: dict[str, SimResult]) -> None:
    r = results["T-10"]
    assert actions(r) == [("land", "GEOFENCE_POLYGON")]
    assert "escalated to LAND" in r.actions[0].note
    after = [f for f in r.frames if f.t > r.actions[0].act_t and f.armed]
    assert after and all(f.monitor_state == MonitorState.LAND for f in after)


def test_event_log_and_report(config: RuleConfig, tmp_path: Path) -> None:
    result = run_scenario(SCENARIOS["T-04"], config, log_dir=tmp_path)
    assert result.log_file is not None and result.log_file.exists()
    events = load_events(result.log_file)
    assert events[0].event_type == EventType.STARTUP
    assert events[-1].event_type == EventType.SHUTDOWN

    report = build_report(events, "T-04")
    assert [a.action for a in report.actions] == ["rtl", "land"]
    assert all(a.time_to_detect_s == pytest.approx(1.0, abs=0.11) for a in report.actions)
    assert report.verdict == "INTERVENED: RTL, LAND"
    assert "BATTERY_LAND" in to_markdown(report)
    assert "<table>" in to_html(report)

    md = write_report(result.log_file)
    assert md.suffix == ".md" and "Time to detect" in md.read_text()
    html_path = write_report(result.log_file, tmp_path / "r.html")
    assert html_path.read_text().startswith("<!doctype html>")


def test_report_empty_log() -> None:
    report = build_report([])
    assert report.verdict == "NOMINAL"
    assert "No failsafe action" in to_markdown(report)


def test_sim_cli_run_writes_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert sim_main(["run", "T-08", "--out", str(tmp_path)]) == 0
    assert "no failsafe action commanded" in capsys.readouterr().out
    assert (tmp_path / "logs" / "events_T-08.jsonl").exists()
    assert (tmp_path / "reports" / "T-08.html").exists()


def test_sim_cli_unknown_scenario(tmp_path: Path) -> None:
    assert sim_main(["run", "T-99", "--out", str(tmp_path)]) == 2


def test_visualize_snapshot(results: dict[str, SimResult], tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from failsafe_monitor import visualize

    r = results["T-01"]
    path = visualize.save_snapshot(r, r.actions[0].act_t, tmp_path / "shot.png")
    assert path.stat().st_size > 10_000
    assert [name for name, _ in visualize.key_moments(r)] == ["action", "final"]
