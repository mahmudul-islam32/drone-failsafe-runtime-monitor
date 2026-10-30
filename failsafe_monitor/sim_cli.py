"""
Command-line interface for the built-in simulator.

Examples:
    failsafe-sim list
    failsafe-sim run T-01 --video --screenshots
    failsafe-sim all --out sim_output --video
    failsafe-sim report sim_output/logs/events_T-01.jsonl -o report.html
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

from .loader import RuleLoadError, load_rules
from .report import build_report, to_html, to_markdown, write_report
from .scenarios import SCENARIOS, get_scenario
from .simulator import SimResult, run_scenario

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "rules" / "example.yaml"


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="failsafe-sim",
        description="Fly scripted scenarios against the monitor without PX4 SITL",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List built-in scenarios")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--rules", "-r", type=Path, default=DEFAULT_RULES,
                       help="Rules YAML (default: rules/example.yaml)")
        p.add_argument("--out", "-o", type=Path, default=Path("sim_output"),
                       help="Output directory (default: sim_output/)")
        p.add_argument("--video", action="store_true", help="Render MP4 video (needs ffmpeg)")
        p.add_argument("--screenshots", action="store_true", help="Save dashboard PNGs")

    run = sub.add_parser("run", help="Run one scenario")
    run.add_argument("scenario", help="Scenario ID, e.g. T-01")
    add_common(run)

    all_ = sub.add_parser("all", help="Run every scenario, write a summary and a demo video")
    add_common(all_)

    rep = sub.add_parser("report", help="Generate an after-flight report from a JSONL log")
    rep.add_argument("log_file", type=Path)
    rep.add_argument("--output", "-o", type=Path, default=None,
                     help="Output .md or .html (default: <log>.report.md)")

    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(args)


def _print_result(result: SimResult) -> None:
    s = result.scenario
    print(f"\n{s.id}  {s.name}")
    print(f"  expected: {s.expected}")
    for ev in result.events:
        if ev.kind in ("warning", "violation", "action", "alert", "mode"):
            print(f"  {ev.t:6.1f}s  {ev.message}")
    if result.actions:
        for a in result.actions:
            print(f"  -> {a.action.upper()} by {a.rule_id}: detect {a.time_to_detect_s:.1f}s "
                  f"after onset{', ' + a.note if a.note else ''}")
    else:
        print("  -> no failsafe action commanded")


def _outputs(result: SimResult, out: Path, video: bool, screenshots: bool) -> None:
    if result.log_file:
        report = build_report(result.log_events, f"{result.scenario.id}: {result.scenario.name}")
        (out / "reports").mkdir(parents=True, exist_ok=True)
        (out / "reports" / f"{result.scenario.id}.md").write_text(to_markdown(report))
        (out / "reports" / f"{result.scenario.id}.html").write_text(to_html(report))
    if not (video or screenshots):
        return
    from . import visualize

    if screenshots:
        (out / "screenshots").mkdir(parents=True, exist_ok=True)
        for name, t in visualize.key_moments(result):
            path = out / "screenshots" / f"{result.scenario.id}_{name}.png"
            visualize.save_snapshot(result, t, path)
            print(f"  screenshot: {path}")
    if video:
        (out / "videos").mkdir(parents=True, exist_ok=True)
        path = out / "videos" / f"{result.scenario.id}.mp4"
        visualize.render_video(result, path)
        print(f"  video: {path}")


def main(args: list[str] | None = None) -> int:
    parsed = parse_args(args)
    logging.basicConfig(level=logging.DEBUG if parsed.verbose else logging.ERROR,
                        format="%(levelname)s %(name)s: %(message)s")
    warnings.filterwarnings("ignore", category=FutureWarning, module="mavsdk")

    if parsed.command == "list":
        for s in SCENARIOS.values():
            print(f"{s.id:5} {s.name:32} {s.expected}")
        return 0

    if parsed.command == "report":
        path = write_report(parsed.log_file, parsed.output)
        print(f"Report written to {path}")
        return 0

    try:
        config = load_rules(parsed.rules)
    except RuleLoadError as e:
        print(f"Invalid rules file: {e}", file=sys.stderr)
        return 1

    out: Path = parsed.out
    out.mkdir(parents=True, exist_ok=True)
    log_dir = out / "logs"

    if parsed.command == "run":
        try:
            scenario = get_scenario(parsed.scenario)
        except KeyError as e:
            print(e.args[0], file=sys.stderr)
            return 2
        result = run_scenario(scenario, config, log_dir=log_dir)
        _print_result(result)
        _outputs(result, out, parsed.video, parsed.screenshots)
        return 0

    results = []
    for scenario in SCENARIOS.values():
        result = run_scenario(scenario, config, log_dir=log_dir)
        _print_result(result)
        _outputs(result, out, parsed.video, parsed.screenshots)
        results.append(result)

    if parsed.video or parsed.screenshots:
        from . import visualize

        overview = visualize.save_overview(results, out / "overview.png")
        print(f"\nOverview: {overview}")
        summary = visualize.summary_card(results)
        summary.savefig(out / "summary.png", facecolor=visualize.BG)
        if parsed.video:
            demo = visualize.render_demo(results, out / "demo.mp4")
            print(f"Demo video: {demo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
