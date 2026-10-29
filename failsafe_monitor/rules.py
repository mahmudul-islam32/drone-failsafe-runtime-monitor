"""
Rule definitions and schema for the failsafe monitor.

Defines Pydantic models for parsing and validating the YAML rule file.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RuleDefaults(BaseModel):
    """Default settings applied to all rules."""

    model_config = ConfigDict(extra="forbid")

    debounce_s: float = Field(default=1.0, ge=0.0, description="Debounce time in seconds")
    hysteresis: float = Field(
        default=0.05, ge=0.0, le=1.0, description="Hysteresis fraction for clearing warnings"
    )


class BaseRule(BaseModel):
    """Base class for all rule types."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Unique rule identifier")
    debounce_s: float | None = Field(default=None, ge=0.0, description="Override debounce time")
    hysteresis: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Override hysteresis"
    )
    enabled: bool = Field(default=True, description="Whether the rule is active")


class GeofencePolygonRule(BaseRule):
    """
    Geofence rule based on an inclusion polygon.

    The vehicle must stay inside the polygon. Crossing the boundary triggers
    the violation action. A warning buffer provides early warning before breach.
    """

    type: Literal["geofence_polygon"] = "geofence_polygon"
    polygon: list[tuple[float, float]] = Field(
        ..., min_length=3, description="List of (lat, lon) vertices in degrees"
    )
    warn_buffer_m: float = Field(default=0.0, ge=0.0, description="Warning buffer in metres")
    on_warn: Literal["log_only", "warn"] = Field(default="warn", description="Action on warning")
    on_violation: Literal["log_only", "warn", "rtl", "land"] = Field(
        default="rtl", description="Action on violation"
    )

    @field_validator("polygon")
    @classmethod
    def validate_polygon(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Validate polygon coordinates are valid lat/lon pairs."""
        for lat, lon in v:
            if not (-90 <= lat <= 90):
                raise ValueError(f"Invalid latitude {lat}, must be in [-90, 90]")
            if not (-180 <= lon <= 180):
                raise ValueError(f"Invalid longitude {lon}, must be in [-180, 180]")
        return v


class AltitudeMaxRule(BaseRule):
    """Maximum altitude ceiling rule."""

    type: Literal["altitude_max"] = "altitude_max"
    max_relative_alt_m: float = Field(..., gt=0, description="Maximum relative altitude in metres")
    warn_buffer_m: float = Field(default=0.0, ge=0.0, description="Warning buffer below ceiling")
    on_warn: Literal["log_only", "warn"] = Field(default="warn", description="Action on warning")
    on_violation: Literal["log_only", "warn", "rtl", "land"] = Field(
        default="rtl", description="Action on violation"
    )


class AltitudeMinRule(BaseRule):
    """Minimum altitude floor rule."""

    type: Literal["altitude_min"] = "altitude_min"
    min_relative_alt_m: float = Field(..., ge=0, description="Minimum relative altitude in metres")
    on_violation: Literal["log_only", "warn", "rtl", "land"] = Field(
        default="warn", description="Action on violation"
    )


class BatteryMinRule(BaseRule):
    """Minimum battery threshold rule."""

    type: Literal["battery_min"] = "battery_min"
    threshold_percent: float = Field(
        ..., ge=0, le=100, description="Battery threshold percentage"
    )
    voltage_floor_v: float | None = Field(
        default=None, gt=0, description="Optional voltage floor"
    )
    on_violation: Literal["log_only", "warn", "rtl", "land"] = Field(
        default="rtl", description="Action on violation"
    )


class TelemetryMaxAgeRule(BaseRule):
    """Telemetry staleness detection rule."""

    type: Literal["telemetry_max_age"] = "telemetry_max_age"
    stream: Literal["position", "battery", "health"] = Field(
        default="position", description="Which telemetry stream to monitor"
    )
    max_age_s: float = Field(..., gt=0, description="Maximum acceptable age in seconds")
    on_violation: Literal["log_only", "warn", "rtl", "land"] = Field(
        default="rtl", description="Action on violation"
    )


class ConnectionLostRule(BaseRule):
    """MAVLink connection loss detection rule."""

    type: Literal["connection_lost"] = "connection_lost"
    on_violation: Literal["log_only", "warn"] = Field(
        default="log_only",
        description="Action on connection loss (monitor is blind, limited actions possible)",
    )


Rule = Annotated[
    GeofencePolygonRule
    | AltitudeMaxRule
    | AltitudeMinRule
    | BatteryMinRule
    | TelemetryMaxAgeRule
    | ConnectionLostRule,
    Field(discriminator="type"),
]


class RuleConfig(BaseModel):
    """
    Complete rule configuration file schema.

    Validates the entire rules.yaml file structure.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1, le=1, description="Schema version")
    vehicle_id: str = Field(default="default", description="Vehicle identifier")
    tick_hz: float = Field(default=10.0, gt=0, le=100, description="Rule evaluation rate")
    defaults: RuleDefaults = Field(default_factory=RuleDefaults)
    rules: list[Rule] = Field(default_factory=list, description="List of safety rules")

    @model_validator(mode="after")
    def validate_unique_rule_ids(self) -> RuleConfig:
        """Ensure all rule IDs are unique."""
        ids = [rule.id for rule in self.rules]
        duplicates = [rid for rid in ids if ids.count(rid) > 1]
        if duplicates:
            raise ValueError(f"Duplicate rule IDs: {set(duplicates)}")
        return self

    def get_rule(self, rule_id: str) -> Rule | None:
        """Get a rule by ID."""
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None

    def get_effective_debounce(self, rule: BaseRule) -> float:
        """Get the effective debounce time for a rule."""
        if rule.debounce_s is not None:
            return rule.debounce_s
        return self.defaults.debounce_s

    def get_effective_hysteresis(self, rule: BaseRule) -> float:
        """Get the effective hysteresis for a rule."""
        if rule.hysteresis is not None:
            return rule.hysteresis
        return self.defaults.hysteresis
