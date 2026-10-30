"""
Action executor for failsafe commands.

Executes RTL and Land commands via MAVSDK with confirmation and retry.
"""

from __future__ import annotations

import asyncio
import logging
import time
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

with warnings.catch_warnings():
    # mavsdk 3.x warns about its upcoming rename to mavsdk-grpc on import.
    warnings.simplefilter("ignore", FutureWarning)
    from mavsdk import System
    from mavsdk.action import ActionError

from .models import (
    ActionRequest,
    ActionResult,
    FlightMode,
    Severity,
)

if TYPE_CHECKING:
    from .telemetry import TelemetrySubscriber

logger = logging.getLogger(__name__)


@dataclass
class ExecutorConfig:
    """Configuration for the action executor."""

    confirmation_timeout_s: float = 2.0
    max_retries: int = 1
    retry_delay_s: float = 0.5
    dry_run: bool = False


class ActionExecutor:
    """
    Executes failsafe actions (RTL, Land) via MAVSDK.

    Implements:
    - Confirmation: waits for flight mode change via telemetry
    - Retry: retries on failure, then escalates to Land
    - Idempotency: skips if already in requested mode
    """

    def __init__(
        self,
        system: System,
        telemetry_subscriber: TelemetrySubscriber,
        config: ExecutorConfig | None = None,
    ) -> None:
        self._system = system
        self._telemetry = telemetry_subscriber
        self.config = config or ExecutorConfig()
        self._last_action: ActionRequest | None = None
        self._pending_escalation: bool = False

    @property
    def is_dry_run(self) -> bool:
        """Return True if in dry-run mode."""
        return self.config.dry_run

    async def execute(self, request: ActionRequest) -> ActionResult:
        """
        Execute a failsafe action.

        Args:
            request: The action request to execute

        Returns:
            ActionResult with success/failure status and timing
        """
        start_time = time.time()

        if request.action < Severity.RTL:
            return ActionResult(
                request=request,
                success=True,
                confirmed=True,
                latency_s=0.0,
                timestamp_s=start_time,
            )

        current_mode = self._telemetry.state.flight_mode

        if self._is_already_in_mode(request.action, current_mode):
            logger.info(
                "Already in %s mode, skipping action",
                current_mode.value,
            )
            return ActionResult(
                request=request,
                success=True,
                confirmed=True,
                latency_s=0.0,
                timestamp_s=start_time,
            )

        if self.config.dry_run:
            logger.info(
                "[DRY RUN] Would execute %s for rule %s",
                request.action.value,
                request.source_rule_id,
            )
            return ActionResult(
                request=request,
                success=True,
                confirmed=True,
                latency_s=0.0,
                timestamp_s=start_time,
            )

        result = await self._execute_with_retry(request, start_time)

        if not result.success and request.action == Severity.RTL:
            logger.warning(
                "RTL failed after retries, escalating to Land"
            )
            self._pending_escalation = True
            land_request = ActionRequest(
                action=Severity.LAND,
                source_rule_id=request.source_rule_id,
                source_violation=request.source_violation,
            )
            result = await self._execute_with_retry(land_request, time.time())
            self._pending_escalation = False

        self._last_action = request
        return result

    def _is_already_in_mode(self, action: Severity, current_mode: FlightMode) -> bool:
        """Check if already in the requested failsafe mode."""
        if action == Severity.RTL:
            return current_mode == FlightMode.RETURN_TO_LAUNCH
        elif action == Severity.LAND:
            return current_mode == FlightMode.LAND
        return False

    async def _execute_with_retry(
        self, request: ActionRequest, start_time: float
    ) -> ActionResult:
        """Execute action with retry logic."""
        last_error: str | None = None
        retry_count = 0

        while retry_count <= self.config.max_retries:
            try:
                success = await self._send_command(request.action)
                if success:
                    confirmed = await self._wait_for_confirmation(request.action)
                    latency = time.time() - start_time

                    if confirmed:
                        logger.info(
                            "Action %s confirmed (latency=%.2fs)",
                            request.action.value,
                            latency,
                        )
                        return ActionResult(
                            request=request,
                            success=True,
                            confirmed=True,
                            retry_count=retry_count,
                            latency_s=latency,
                            timestamp_s=start_time,
                        )
                    else:
                        last_error = "Mode change not confirmed within timeout"
                        logger.warning(
                            "Action %s not confirmed after %.1fs",
                            request.action.value,
                            self.config.confirmation_timeout_s,
                        )
                else:
                    last_error = "Command returned failure"
            except ActionError as e:
                last_error = str(e)
                logger.warning("Action command failed: %s", e)
            except Exception as e:
                last_error = str(e)
                logger.error("Unexpected error executing action: %s", e)

            retry_count += 1
            if retry_count <= self.config.max_retries:
                logger.info(
                    "Retrying action %s (attempt %d/%d)",
                    request.action.value,
                    retry_count + 1,
                    self.config.max_retries + 1,
                )
                await asyncio.sleep(self.config.retry_delay_s)

        latency = time.time() - start_time
        return ActionResult(
            request=request,
            success=False,
            confirmed=False,
            retry_count=retry_count,
            latency_s=latency,
            error_message=last_error,
            timestamp_s=start_time,
        )

    async def _send_command(self, action: Severity) -> bool:
        """Send the action command to the vehicle."""
        try:
            if action == Severity.RTL:
                logger.info("Commanding Return-to-Launch")
                await self._system.action.return_to_launch()
                return True
            elif action == Severity.LAND:
                logger.info("Commanding Land")
                await self._system.action.land()
                return True
            else:
                logger.warning("Unknown action severity: %s", action)
                return False
        except ActionError as e:
            logger.error("MAVSDK action error: %s", e)
            raise

    async def _wait_for_confirmation(self, action: Severity) -> bool:
        """Wait for flight mode to change to confirm action."""
        target_mode: FlightMode
        if action == Severity.RTL:
            target_mode = FlightMode.RETURN_TO_LAUNCH
        elif action == Severity.LAND:
            target_mode = FlightMode.LAND
        else:
            return True

        try:
            async with asyncio.timeout(self.config.confirmation_timeout_s):
                while True:
                    current_mode = self._telemetry.state.flight_mode
                    if current_mode == target_mode:
                        return True
                    await asyncio.sleep(0.1)
        except asyncio.TimeoutError:
            return False


async def create_action_executor(
    telemetry_subscriber: TelemetrySubscriber,
    dry_run: bool = False,
    confirmation_timeout_s: float = 2.0,
) -> ActionExecutor | None:
    """
    Create an action executor from a telemetry subscriber.

    Args:
        telemetry_subscriber: Connected telemetry subscriber
        dry_run: If True, log actions without executing
        confirmation_timeout_s: Timeout for mode confirmation

    Returns:
        ActionExecutor or None if not connected
    """
    if not telemetry_subscriber.system:
        logger.error("Cannot create executor: no MAVSDK system")
        return None

    config = ExecutorConfig(
        dry_run=dry_run,
        confirmation_timeout_s=confirmation_timeout_s,
    )

    return ActionExecutor(
        system=telemetry_subscriber.system,
        telemetry_subscriber=telemetry_subscriber,
        config=config,
    )
