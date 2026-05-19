"""Unified block for Uncertainty Module."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union

from utils.utils import (
    coerce_bool,
    coerce_float,
    console_log,
    load_schema as _load_schema,
    load_config as _load_config,
    save_config as _save_config,
    now_utc,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MODULE_FILE = "UncertaintyModule"

SPEED_OF_LIGHT_M_PER_S = 299_792_458.0


# ─────────────────────────────────────────────────────────────────────────────
# Schema / Config loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_schema() -> Dict[str, Any]:
    """Load the block schema from JSON."""
    return _load_schema(MODULE_FILE)


def load_config() -> Dict[str, Any]:
    """Load current config values."""
    return _load_config(MODULE_FILE)


def save_config(config: Dict[str, Any]) -> None:
    """Persist config values."""
    _save_config(MODULE_FILE, config)


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _meters_to_milliseconds(value: float) -> float:
    """Convert meters to milliseconds using speed of light."""
    return float(value) / SPEED_OF_LIGHT_M_PER_S * 1_000.0


def _debug(msg: str) -> None:
    console_log("UncertaintyModule", msg)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline_legacy(config: Optional[Mapping[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """Legacy raw computation. Returns the flat dict shape used by older
    callers; the 1.0 run_pipeline below wraps this into outputs/summary/data."""
    # Load config from this block's JSON and merge with overrides
    cfg = load_config() or {}
    if config:
        cfg.update(config)
    
    data = dict(cfg)
    
    # Read enabled from config
    enabled = coerce_bool(data.get("enabled"), False)
    
    # Support legacy fields (meters instead of milliseconds)
    legacy_clock_m = data.get("sigma_clock_m")
    legacy_range_m = data.get("sigma_range_m")
    satellite_ms = data.get("satellite_clock_uncertainty_ms")
    propagation_ms = data.get("propagation_uncertainty_ms")
    
    if satellite_ms is None and legacy_clock_m is not None:
        satellite_ms = _meters_to_milliseconds(coerce_float(legacy_clock_m, 0.0))
    if propagation_ms is None and legacy_range_m is not None:
        propagation_ms = _meters_to_milliseconds(coerce_float(legacy_range_m, 0.0))
    
    _debug(f"Pipeline params: enabled={enabled}")
    
    # Get parameters with defaults
    satellite_clock_ms = coerce_float(satellite_ms, 0.05)
    receiver_clock_ms = coerce_float(data.get("receiver_clock_uncertainty_ms"), 0.10)
    propagation_ms_val = coerce_float(propagation_ms, 0.25)
    
    # Calculate components
    components = {
        "satellite_clock_ms": satellite_clock_ms,
        "receiver_clock_ms": receiver_clock_ms,
        "propagation_ms": propagation_ms_val,
    }
    
    # Total uncertainty (only if enabled)
    total = (satellite_clock_ms + receiver_clock_ms + propagation_ms_val) if enabled else 0.0
    
    return {
        "enabled": enabled,
        "total_uncertainty_ms": total,
        "components": components,
        "executed_at": now_utc().isoformat() + "Z",
    }


def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point. consumes=[], produces=['total_uncertainty_ms']."""
    raw = _run_pipeline_legacy(config=config)
    return {
        "outputs": {
            "total_uncertainty_ms": raw.get("total_uncertainty_ms", 0.0),
        },
        "summary": {
            "enabled": raw.get("enabled"),
            "total_uncertainty_ms": raw.get("total_uncertainty_ms"),
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


def run_pipeline_from_json(payload: Union[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Execute the uncertainty pipeline from a JSON-compatible payload."""
    import json

    if isinstance(payload, str):
        try:
            config_map = json.loads(payload.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload for uncertainty pipeline") from exc
    elif isinstance(payload, Mapping):
        config_map = payload
    else:
        raise TypeError("Uncertainty payload must be a mapping or JSON string")

    return run_pipeline(config_map)


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "load_schema",
    "load_config",
    "save_config",
    "run_pipeline",
    "run_pipeline_from_json",
    "SPEED_OF_LIGHT_M_PER_S",
]
