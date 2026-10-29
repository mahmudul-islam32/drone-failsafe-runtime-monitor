"""Tests for rule file loader."""

from pathlib import Path

import pytest

from failsafe_monitor.loader import RuleLoadError, load_rules, validate_rules_file


class TestLoadRules:
    """Tests for load_rules function."""

    def test_load_valid_file(self, tmp_path: Path) -> None:
        """Test loading a valid rule file."""
        rule_file = tmp_path / "rules.yaml"
        rule_file.write_text("""
version: 1
vehicle_id: test-drone
tick_hz: 10
rules:
  - id: GEO
    type: geofence_polygon
    polygon:
      - [47.0, 8.0]
      - [47.1, 8.0]
      - [47.1, 8.1]
  - id: BAT
    type: battery_min
    threshold_percent: 25
""")
        config = load_rules(rule_file)
        assert config.vehicle_id == "test-drone"
        assert len(config.rules) == 2
        assert config.rules[0].id == "GEO"
        assert config.rules[1].id == "BAT"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Test loading a nonexistent file raises error."""
        with pytest.raises(RuleLoadError) as exc_info:
            load_rules(tmp_path / "nonexistent.yaml")
        assert "not found" in str(exc_info.value).lower()

    def test_load_directory_raises_error(self, tmp_path: Path) -> None:
        """Test loading a directory raises error."""
        with pytest.raises(RuleLoadError) as exc_info:
            load_rules(tmp_path)
        assert "not a file" in str(exc_info.value).lower()

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        """Test loading invalid YAML raises error."""
        rule_file = tmp_path / "bad.yaml"
        rule_file.write_text("""
version: 1
rules:
  - invalid: yaml: content
    [broken
""")
        with pytest.raises(RuleLoadError) as exc_info:
            load_rules(rule_file)
        assert "invalid yaml" in str(exc_info.value).lower()

    def test_load_empty_file(self, tmp_path: Path) -> None:
        """Test loading empty file raises error."""
        rule_file = tmp_path / "empty.yaml"
        rule_file.write_text("")
        with pytest.raises(RuleLoadError) as exc_info:
            load_rules(rule_file)
        assert "empty" in str(exc_info.value).lower()

    def test_load_non_mapping_file(self, tmp_path: Path) -> None:
        """Test loading file with non-mapping content raises error."""
        rule_file = tmp_path / "list.yaml"
        rule_file.write_text("- item1\n- item2\n")
        with pytest.raises(RuleLoadError) as exc_info:
            load_rules(rule_file)
        assert "mapping" in str(exc_info.value).lower()

    def test_load_validation_error(self, tmp_path: Path) -> None:
        """Test loading file with schema violations raises error."""
        rule_file = tmp_path / "invalid.yaml"
        rule_file.write_text("""
version: 1
rules:
  - id: BAT
    type: battery_min
    # missing required threshold_percent
""")
        with pytest.raises(RuleLoadError) as exc_info:
            load_rules(rule_file)
        assert "validation failed" in str(exc_info.value).lower()
        assert "threshold_percent" in str(exc_info.value).lower()

    def test_load_unknown_keys_rejected(self, tmp_path: Path) -> None:
        """Test that unknown keys are rejected by strict validation."""
        rule_file = tmp_path / "unknown.yaml"
        rule_file.write_text("""
version: 1
unknown_top_level_key: should_fail
rules: []
""")
        with pytest.raises(RuleLoadError):
            load_rules(rule_file)

    def test_load_disabled_rule(self, tmp_path: Path) -> None:
        """Test loading file with disabled rule."""
        rule_file = tmp_path / "disabled.yaml"
        rule_file.write_text("""
version: 1
rules:
  - id: BAT_ENABLED
    type: battery_min
    threshold_percent: 25
  - id: BAT_DISABLED
    type: battery_min
    threshold_percent: 10
    enabled: false
""")
        config = load_rules(rule_file)
        assert len(config.rules) == 2
        enabled = [r for r in config.rules if r.enabled]
        disabled = [r for r in config.rules if not r.enabled]
        assert len(enabled) == 1
        assert len(disabled) == 1
        assert disabled[0].id == "BAT_DISABLED"

    def test_load_with_custom_defaults(self, tmp_path: Path) -> None:
        """Test loading file with custom defaults."""
        rule_file = tmp_path / "defaults.yaml"
        rule_file.write_text("""
version: 1
defaults:
  debounce_s: 2.5
  hysteresis: 0.1
rules:
  - id: BAT
    type: battery_min
    threshold_percent: 25
""")
        config = load_rules(rule_file)
        assert config.defaults.debounce_s == 2.5
        assert config.defaults.hysteresis == 0.1

    def test_load_string_path(self, tmp_path: Path) -> None:
        """Test loading with string path."""
        rule_file = tmp_path / "rules.yaml"
        rule_file.write_text("version: 1\nrules: []")
        config = load_rules(str(rule_file))
        assert config.version == 1


class TestValidateRulesFile:
    """Tests for validate_rules_file function."""

    def test_valid_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Test valid file returns empty error list."""
        rule_file = tmp_path / "valid.yaml"
        rule_file.write_text("version: 1\nrules: []")
        errors = validate_rules_file(rule_file)
        assert errors == []

    def test_invalid_file_returns_errors(self, tmp_path: Path) -> None:
        """Test invalid file returns error list."""
        rule_file = tmp_path / "invalid.yaml"
        rule_file.write_text("version: 99\nrules: []")
        errors = validate_rules_file(rule_file)
        assert len(errors) == 1
        assert "version" in errors[0].lower()

    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        """Test missing file returns error."""
        errors = validate_rules_file(tmp_path / "missing.yaml")
        assert len(errors) == 1
        assert "not found" in errors[0].lower()


class TestExampleRulesFile:
    """Test the example rules file."""

    def test_example_file_is_valid(self) -> None:
        """Test that the example rules file loads successfully."""
        example_path = Path(__file__).parent.parent / "rules" / "example.yaml"
        if example_path.exists():
            config = load_rules(example_path)
            assert config.vehicle_id == "sitl-x500"
            assert config.tick_hz == 10
            assert len(config.rules) >= 5

            rule_ids = [r.id for r in config.rules]
            assert "GEOFENCE_POLYGON" in rule_ids
            assert "ALT_CEILING" in rule_ids
            assert "BATTERY_RTL" in rule_ids
            assert "BATTERY_LAND" in rule_ids
            assert "TELEMETRY_STALE" in rule_ids
