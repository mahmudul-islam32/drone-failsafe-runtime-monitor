"""Tests for CLI."""

import tempfile
from pathlib import Path

import pytest

from failsafe_monitor.cli import parse_args


class TestParseArgs:
    """Tests for argument parsing."""

    @pytest.fixture
    def temp_rules_file(self) -> Path:
        """Create a temporary rules file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("version: 1\nrules: []\n")
            yield Path(f.name)

    def test_required_rules_arg(self) -> None:
        """Test that --rules is required."""
        with pytest.raises(SystemExit):
            parse_args([])

    def test_default_values(self, temp_rules_file: Path) -> None:
        """Test default argument values."""
        args = parse_args(["--rules", str(temp_rules_file)])

        assert args.connect == "udp://:14540"
        assert args.log_dir == Path("logs")
        assert args.dry_run is False
        assert args.verbose is False
        assert args.timeout == 30.0
        assert args.validate is False

    def test_custom_connection(self, temp_rules_file: Path) -> None:
        """Test custom connection URL."""
        args = parse_args([
            "--rules", str(temp_rules_file),
            "--connect", "udp://:14550",
        ])
        assert args.connect == "udp://:14550"

    def test_short_flags(self, temp_rules_file: Path) -> None:
        """Test short flag versions."""
        args = parse_args([
            "-r", str(temp_rules_file),
            "-c", "udp://:14550",
            "-v",
        ])
        assert args.rules == temp_rules_file
        assert args.connect == "udp://:14550"
        assert args.verbose is True

    def test_dry_run_flag(self, temp_rules_file: Path) -> None:
        """Test dry-run flag."""
        args = parse_args([
            "--rules", str(temp_rules_file),
            "--dry-run",
        ])
        assert args.dry_run is True

    def test_validate_flag(self, temp_rules_file: Path) -> None:
        """Test validate flag."""
        args = parse_args([
            "--rules", str(temp_rules_file),
            "--validate",
        ])
        assert args.validate is True

    def test_timeout_option(self, temp_rules_file: Path) -> None:
        """Test custom timeout."""
        args = parse_args([
            "--rules", str(temp_rules_file),
            "--timeout", "60",
        ])
        assert args.timeout == 60.0

    def test_log_dir_option(self, temp_rules_file: Path) -> None:
        """Test custom log directory."""
        args = parse_args([
            "--rules", str(temp_rules_file),
            "--log-dir", "/tmp/custom_logs",
        ])
        assert args.log_dir == Path("/tmp/custom_logs")
