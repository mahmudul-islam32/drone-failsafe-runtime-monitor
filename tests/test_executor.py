"""Tests for action executor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from failsafe_monitor.executor import (
    ActionExecutor,
    ExecutorConfig,
    create_action_executor,
)
from failsafe_monitor.models import (
    ActionRequest,
    FlightMode,
    Severity,
    VehicleState,
    Violation,
)


@pytest.fixture
def mock_violation() -> Violation:
    """Create a mock violation for testing."""
    return Violation(
        rule_id="TEST_RULE",
        severity=Severity.RTL,
        message="Test violation",
    )


@pytest.fixture
def mock_telemetry() -> MagicMock:
    """Create a mock telemetry subscriber."""
    telemetry = MagicMock()
    telemetry.state = VehicleState(flight_mode=FlightMode.MISSION)
    telemetry.system = MagicMock()
    return telemetry


@pytest.fixture
def mock_system() -> MagicMock:
    """Create a mock MAVSDK system."""
    system = MagicMock()
    system.action.return_to_launch = AsyncMock()
    system.action.land = AsyncMock()
    return system


class TestExecutorConfig:
    """Tests for ExecutorConfig."""

    def test_default_config(self) -> None:
        """Test default configuration."""
        config = ExecutorConfig()
        assert config.confirmation_timeout_s == 2.0
        assert config.max_retries == 1
        assert config.retry_delay_s == 0.5
        assert config.dry_run is False

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = ExecutorConfig(
            confirmation_timeout_s=5.0,
            max_retries=3,
            dry_run=True,
        )
        assert config.confirmation_timeout_s == 5.0
        assert config.max_retries == 3
        assert config.dry_run is True


class TestActionExecutor:
    """Tests for ActionExecutor."""

    def test_is_dry_run(self, mock_system: MagicMock, mock_telemetry: MagicMock) -> None:
        """Test dry_run property."""
        config = ExecutorConfig(dry_run=True)
        executor = ActionExecutor(mock_system, mock_telemetry, config)
        assert executor.is_dry_run is True

    @pytest.mark.asyncio
    async def test_skip_non_action_severity(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test that warnings don't trigger actual commands."""
        executor = ActionExecutor(mock_system, mock_telemetry)
        request = ActionRequest(
            action=Severity.WARN,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )

        result = await executor.execute(request)

        assert result.success is True
        assert result.confirmed is True
        mock_system.action.return_to_launch.assert_not_called()
        mock_system.action.land.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_if_already_in_mode(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test skipping action if already in target mode."""
        mock_telemetry.state.flight_mode = FlightMode.RETURN_TO_LAUNCH
        executor = ActionExecutor(mock_system, mock_telemetry)
        request = ActionRequest(
            action=Severity.RTL,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )

        result = await executor.execute(request)

        assert result.success is True
        assert result.confirmed is True
        mock_system.action.return_to_launch.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_mode(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test dry run mode doesn't send commands."""
        config = ExecutorConfig(dry_run=True)
        executor = ActionExecutor(mock_system, mock_telemetry, config)
        request = ActionRequest(
            action=Severity.RTL,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )

        result = await executor.execute(request)

        assert result.success is True
        mock_system.action.return_to_launch.assert_not_called()

    @pytest.mark.asyncio
    async def test_rtl_with_confirmation(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test RTL command with successful confirmation."""
        config = ExecutorConfig(confirmation_timeout_s=0.5)
        executor = ActionExecutor(mock_system, mock_telemetry, config)

        async def simulate_mode_change():
            await asyncio.sleep(0.1)
            mock_telemetry.state.flight_mode = FlightMode.RETURN_TO_LAUNCH

        asyncio.create_task(simulate_mode_change())

        request = ActionRequest(
            action=Severity.RTL,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )

        result = await executor.execute(request)

        assert result.success is True
        assert result.confirmed is True
        mock_system.action.return_to_launch.assert_called_once()

    @pytest.mark.asyncio
    async def test_land_command(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test Land command."""
        config = ExecutorConfig(confirmation_timeout_s=0.5)
        executor = ActionExecutor(mock_system, mock_telemetry, config)

        async def simulate_mode_change():
            await asyncio.sleep(0.1)
            mock_telemetry.state.flight_mode = FlightMode.LAND

        asyncio.create_task(simulate_mode_change())

        request = ActionRequest(
            action=Severity.LAND,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )

        result = await executor.execute(request)

        assert result.success is True
        mock_system.action.land.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_timeout(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test retry logic when confirmation times out."""
        config = ExecutorConfig(
            confirmation_timeout_s=0.1,
            max_retries=1,
            retry_delay_s=0.05,
        )
        executor = ActionExecutor(mock_system, mock_telemetry, config)
        request = ActionRequest(
            action=Severity.LAND,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )

        result = await executor.execute(request)

        assert result.success is False
        assert result.confirmed is False
        assert result.retry_count > 0
        assert mock_system.action.land.call_count == 2

    @pytest.mark.asyncio
    async def test_escalate_rtl_to_land(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test escalation from RTL to Land on failure."""
        config = ExecutorConfig(
            confirmation_timeout_s=0.1,
            max_retries=0,
            retry_delay_s=0.01,
        )
        executor = ActionExecutor(mock_system, mock_telemetry, config)

        async def simulate_land_mode():
            await asyncio.sleep(0.05)
            mock_telemetry.state.flight_mode = FlightMode.LAND

        asyncio.create_task(simulate_land_mode())

        request = ActionRequest(
            action=Severity.RTL,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )

        await executor.execute(request)

        mock_system.action.return_to_launch.assert_called_once()
        mock_system.action.land.assert_called()


class TestIdempotency:
    """Tests for action idempotency."""

    @pytest.mark.asyncio
    async def test_already_in_rtl_mode(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test no command sent when already in RTL."""
        mock_telemetry.state.flight_mode = FlightMode.RETURN_TO_LAUNCH
        executor = ActionExecutor(mock_system, mock_telemetry)

        request = ActionRequest(
            action=Severity.RTL,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )
        result = await executor.execute(request)

        assert result.success is True
        assert result.latency_s == 0.0
        mock_system.action.return_to_launch.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_in_land_mode(
        self, mock_system: MagicMock, mock_telemetry: MagicMock, mock_violation: Violation
    ) -> None:
        """Test no command sent when already in Land."""
        mock_telemetry.state.flight_mode = FlightMode.LAND
        executor = ActionExecutor(mock_system, mock_telemetry)

        request = ActionRequest(
            action=Severity.LAND,
            source_rule_id="TEST",
            source_violation=mock_violation,
        )
        result = await executor.execute(request)

        assert result.success is True
        mock_system.action.land.assert_not_called()


class TestCreateActionExecutor:
    """Tests for create_action_executor factory."""

    @pytest.mark.asyncio
    async def test_create_without_system(self) -> None:
        """Test factory returns None without system."""
        telemetry = MagicMock()
        telemetry.system = None

        executor = await create_action_executor(telemetry)

        assert executor is None

    @pytest.mark.asyncio
    async def test_create_with_system(self, mock_telemetry: MagicMock) -> None:
        """Test factory creates executor with system."""
        executor = await create_action_executor(mock_telemetry)

        assert executor is not None
        assert executor.is_dry_run is False

    @pytest.mark.asyncio
    async def test_create_dry_run(self, mock_telemetry: MagicMock) -> None:
        """Test factory creates dry-run executor."""
        executor = await create_action_executor(mock_telemetry, dry_run=True)

        assert executor is not None
        assert executor.is_dry_run is True
