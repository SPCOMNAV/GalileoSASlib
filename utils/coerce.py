"""
utils/coerce.py
---------------
Type coercion functions used across all pipeline blocks.

All functions accept any input type and return a safe, typed value.
They never raise exceptions — they fall back to a default on failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union


# ─────────────────────────────────────────────────────────────────────────────
# Boolean
# ─────────────────────────────────────────────────────────────────────────────

def coerce_bool(value: Any, default: bool = False) -> bool:
    """
    Convert any value to boolean.

    Accepts: True/False, 1/0, "true"/"false", "yes"/"no", "on"/"off",
    "t"/"f", "y"/"n", etc.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on", "t", "y"}:
            return True
        if lowered in {"false", "0", "no", "off", "f", "n", ""}:
            return False
    return default


# ─────────────────────────────────────────────────────────────────────────────
# Integer
# ─────────────────────────────────────────────────────────────────────────────

def coerce_int(
    value: Any,
    default: int = 0,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """
    Convert any value to integer with optional bounds.

    Args:
        value: The value to convert.
        default: Default value if conversion fails.
        minimum: Optional lower bound (inclusive).
        maximum: Optional upper bound (inclusive).
    """
    if value is None:
        return default
    try:
        result = int(float(value))  # float() first to handle "3.0"
    except (ValueError, TypeError):
        return default

    if minimum is not None and result < minimum:
        return minimum
    if maximum is not None and result > maximum:
        return maximum
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Float
# ─────────────────────────────────────────────────────────────────────────────

def coerce_float(
    value: Any,
    default: float = 0.0,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """
    Convert any value to float with optional bounds.

    Args:
        value: The value to convert.
        default: Default value if conversion fails or result is NaN/Inf.
        minimum: Optional lower bound (inclusive).
        maximum: Optional upper bound (inclusive).
    """
    import math
    if value is None:
        return default
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
    except (ValueError, TypeError):
        return default

    if minimum is not None and result < minimum:
        return minimum
    if maximum is not None and result > maximum:
        return maximum
    return result


# ─────────────────────────────────────────────────────────────────────────────
# String
# ─────────────────────────────────────────────────────────────────────────────

def coerce_string(value: Any, default: str = "") -> str:
    """Convert any value to string. Returns default if value is None."""
    if value is None:
        return default
    return str(value)


def coerce_optional_string(value: Any, default: Optional[str] = None) -> Optional[str]:
    """
    Convert a value to a stripped string, or return None if empty.

    Returns default if value is None or results in an empty string.
    """
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


# ─────────────────────────────────────────────────────────────────────────────
# Path
# ─────────────────────────────────────────────────────────────────────────────

def coerce_path(value: Any, default: Optional[Path] = None) -> Optional[Path]:
    """Convert a value to Path, or return None/default if empty."""
    if value is None:
        return default
    if isinstance(value, Path):
        return value
    s = str(value).strip()
    return Path(s) if s else default


# ─────────────────────────────────────────────────────────────────────────────
# List
# ─────────────────────────────────────────────────────────────────────────────

def coerce_list(
    value: Any,
    default: Optional[List] = None,
    item_coercer: Optional[Callable[[Any], Any]] = None,
) -> List:
    """
    Convert a value to list with optional per-item coercion.

    Supports JSON array strings, comma-separated values, and iterables.
    """
    if default is None:
        default = []

    if value is None:
        return default

    if isinstance(value, str):
        value = value.strip()
        if value.startswith("["):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return default
        elif "," in value:
            value = [v.strip() for v in value.split(",") if v.strip()]
        else:
            value = [value] if value else default

    if not isinstance(value, (list, tuple)):
        return default

    if item_coercer:
        return [item_coercer(item) for item in value]
    return list(value)


# ─────────────────────────────────────────────────────────────────────────────
# Dict
# ─────────────────────────────────────────────────────────────────────────────

def coerce_dict(value: Any, default: Optional[Dict] = None) -> Dict:
    """
    Convert a value to dictionary.

    Supports JSON object strings and dict-like objects.
    """
    if default is None:
        default = {}

    if value is None:
        return default

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return default


# ─────────────────────────────────────────────────────────────────────────────
# Aliases
# ─────────────────────────────────────────────────────────────────────────────

def parse_bool(value: Any) -> bool:
    """Alias for coerce_bool (backward compatibility)."""
    return coerce_bool(value)


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "coerce_bool",
    "coerce_int",
    "coerce_float",
    "coerce_string",
    "coerce_optional_string",
    "coerce_path",
    "coerce_list",
    "coerce_dict",
    "parse_bool",
]
