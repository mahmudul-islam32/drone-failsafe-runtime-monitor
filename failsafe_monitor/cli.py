"""
Command-line interface for the failsafe monitor.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import signal
import sys
from pathlib import Path
from typing import NoReturn

from . import __version__
from .engine import RuleEngine
from .executor import ActionExecutor, ExecutorConfig
from .loader import RuleLoadError, load_rules
from .logging import EventLogger
from .telemetry import TelemetryConfig, TelemetrySubscriber


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("mavsdk").setLevel(logging.WARNING)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="failsafe-monitor",
        description="Independent runtime monitor for PX4 drones in SITL simulation",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "--connect",
        "-c",
        default="udp://:14540",
        help="MAVSDK connection URL (default: udp://:14540)",
    )

    parser.add_argument(
        "--rules",
        "-r",
        type=Path,
        required=True,
        help="Path to the YAML rules file",
    )

    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory for event logs (default: logs/)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log violations without sending commands",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Connection timeout in seconds (default: 30)",
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the rules file and exit",
    )

    return parser.parse_args(args)


async def run_monitor(
    connection_url: str,
    rules_path: Path,
    log_dir: Path,
    dry_run: bool = False,
    timeout: float = 30.0,
) -> int:
    """
    Run the failsafe monitor.

    Args:
        connection_url: MAVSDK connection URL
        rules_path: Path to rules YAML file
        log_dir: Directory for event logs
        dry_run: If True, don't send commands
        timeout: Connection timeout in seconds

    Returns:
        Exit code (0 for success)
    """
    logger = logging.getLogger(__name__)

    try:
        config = load_rules(rules_path)
    except RuleLoadError as e:
        logger.error("Failed to load rules: %s", e)
        return 1

    rules_hash = hashlib.sha256(rules_path.read_bytes()).hexdigest()[:16]

    event_logger = EventLogger(log_dir=log_dir)
    event_logger.open()

    try:
        event_logger.log_startup(
            config_hash=rules_hash,
            rules_file=str(rules_path),
            vehicle_id=config.vehicle_id,
        )

        telemetry_config = TelemetryConfig(
            connection_url=connection_url,
            connection_timeout_s=timeout,
        )
        telemetry = TelemetrySubscriber(telemetry_config)

        logger.info("Connecting to %s...", connection_url)
        if not await telemetry.connect():
            logger.error("Failed to connect to vehicle")
            event_logger.log_error("Connection failed")
            return 1

        event_logger.log_connected()

        await telemetry.start()

        engine = RuleEngine(config)

        executor_config = ExecutorConfig(dry_run=dry_run)
        executor = ActionExecutor(
            system=telemetry.system,  # type: ignore
            telemetry_subscriber=telemetry,
            config=executor_config,
        )

        if dry_run:
            logger.info("Running in DRY-RUN mode - no commands will be sent")
            event_logger.log_warning("Dry-run mode enabled")

        shutdown_event = asyncio.Event()

        def signal_handler() -> None:
            logger.info("Shutdown requested")
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)

        tick_interval = 1.0 / config.tick_hz
        last_state = engine.monitor_state

        logger.info(
            "Monitor running (tick_hz=%.1f, %d rules, %s)",
            config.tick_hz,
            len([r for r in config.rules if r.enabled]),
            "dry-run" if dry_run else "live",
        )

        try:
            while not shutdown_event.is_set():
                violations = engine.tick(telemetry.state)

                current_state = engine.monitor_state
                if current_state != last_state:
                    event_logger.log_state_change(
                        last_state.value,
                        current_state.value,
                    )
                    last_state = current_state

                for violation in violations:
                    event_logger.log_violation(
                        rule_id=violation.rule_id,
                        severity=violation.severity.value,
                        message=violation.message,
                        values=violation.values,
                    )

                action_request = engine.get_action_request(violations)
                if action_request:
                    event_logger.log_action_requested(
                        action=action_request.action.value,
                        rule_id=action_request.source_rule_id,
                        severity=action_request.action.value,
                    )

                    result = await executor.execute(action_request)

                    event_logger.log_action_executed(
                        action=action_request.action.value,
                        success=result.success,
                        latency_s=result.latency_s,
                        retry_count=result.retry_count,
                    )

                await asyncio.sleep(tick_interval)

        except asyncio.CancelledError:
            logger.info("Monitor cancelled")

        await telemetry.stop()
        event_logger.log_shutdown("normal")

        summary = event_logger.get_summary()
        logger.info(
            "Session complete: %d events, %d violations, %d actions",
            summary.get("total_events", 0),
            summary.get("violations", 0),
            summary.get("actions_attempted", 0),
        )

        return 0

    except Exception as e:
        logger.exception("Monitor error: %s", e)
        event_logger.log_error(f"Fatal error: {e}")
        return 1

    finally:
        event_logger.close()


def main(args: list[str] | None = None) -> NoReturn:
    """Main entry point for the CLI."""
    parsed = parse_args(args)
    setup_logging(parsed.verbose)

    if parsed.validate:
        try:
            config = load_rules(parsed.rules)
            print(f"Rules file is valid: {len(config.rules)} rules loaded")
            print(f"  Vehicle ID: {config.vehicle_id}")
            print(f"  Tick rate: {config.tick_hz} Hz")
            enabled = [r for r in config.rules if r.enabled]
            disabled = [r for r in config.rules if not r.enabled]
            print(f"  Enabled rules: {len(enabled)}")
            for rule in enabled:
                print(f"    - {rule.id} ({rule.type})")
            if disabled:
                print(f"  Disabled rules: {len(disabled)}")
            sys.exit(0)
        except RuleLoadError as e:
            print(f"Invalid rules file: {e}", file=sys.stderr)
            sys.exit(1)

    exit_code = asyncio.run(
        run_monitor(
            connection_url=parsed.connect,
            rules_path=parsed.rules,
            log_dir=parsed.log_dir,
            dry_run=parsed.dry_run,
            timeout=parsed.timeout,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
