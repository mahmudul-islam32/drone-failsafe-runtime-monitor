"""
Rule file loader and validator.

Loads YAML rule files and validates them against the Pydantic schema.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from .rules import RuleConfig

logger = logging.getLogger(__name__)


class RuleLoadError(Exception):
    """Raised when a rule file cannot be loaded or validated."""

    pass


def load_rules(path: Path | str) -> RuleConfig:
    """
    Load and validate a YAML rule file.

    Args:
        path: Path to the YAML rule file.

    Returns:
        Validated RuleConfig object.

    Raises:
        RuleLoadError: If the file cannot be read or validation fails.
    """
    path = Path(path)

    if not path.exists():
        raise RuleLoadError(f"Rule file not found: {path}")

    if not path.is_file():
        raise RuleLoadError(f"Rule path is not a file: {path}")

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuleLoadError(f"Failed to read rule file {path}: {e}") from e

    file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    logger.info("Loading rule file %s (hash: %s)", path, file_hash)

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise RuleLoadError(f"Invalid YAML in {path}: {e}") from e

    if data is None:
        raise RuleLoadError(f"Rule file {path} is empty")

    if not isinstance(data, dict):
        raise RuleLoadError(f"Rule file {path} must contain a YAML mapping")

    try:
        config = RuleConfig.model_validate(data)
    except ValidationError as e:
        errors = []
        for error in e.errors():
            loc = " -> ".join(str(x) for x in error["loc"])
            msg = error["msg"]
            errors.append(f"  {loc}: {msg}")
        error_str = "\n".join(errors)
        raise RuleLoadError(f"Validation failed for {path}:\n{error_str}") from e

    logger.info(
        "Loaded %d rules for vehicle '%s' (tick_hz=%.1f)",
        len(config.rules),
        config.vehicle_id,
        config.tick_hz,
    )

    enabled_rules = [r for r in config.rules if r.enabled]
    disabled_rules = [r for r in config.rules if not r.enabled]

    if disabled_rules:
        logger.info("Disabled rules: %s", [r.id for r in disabled_rules])

    for rule in enabled_rules:
        logger.debug("  Rule %s (%s): enabled", rule.id, rule.type)

    return config


def validate_rules_file(path: Path | str) -> list[str]:
    """
    Validate a rule file and return a list of errors.

    Args:
        path: Path to the YAML rule file.

    Returns:
        List of error messages, empty if valid.
    """
    try:
        load_rules(path)
        return []
    except RuleLoadError as e:
        return [str(e)]
