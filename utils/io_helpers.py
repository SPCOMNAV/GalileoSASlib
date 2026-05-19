"""
utils/io_helpers.py
-------------------
JSON file I/O, serialization, schema/config helpers, and generic
datetime utilities (not tied to any specific time system).
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.paths import SCHEMA_DIR, CONFIG_DIR


# ─────────────────────────────────────────────────────────────────────────────
# Generic DateTime Helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO 8601 datetime string to a datetime object.

    Supports formats with and without 'Z' suffix for UTC.

    Args:
        value: ISO 8601 string (e.g., "2025-01-01T12:00:00Z").

    Returns:
        Parsed datetime object, or None if value is empty or invalid.
    """
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def now_utc() -> datetime:
    """Return the current datetime in UTC."""
    return datetime.now(timezone.utc)


def format_timestamp(dt: Optional[datetime] = None, fmt: str = "%Y%m%dT%H%M%SZ") -> str:
    """
    Format a datetime to string using the specified format.

    Args:
        dt: Datetime to format. If None, uses current UTC time.
        fmt: strftime format string. Default is compact ISO format.
    """
    if dt is None:
        dt = now_utc()
    return dt.strftime(fmt)


# ─────────────────────────────────────────────────────────────────────────────
# Serialization
# ─────────────────────────────────────────────────────────────────────────────

def serialise_value(value: Any) -> Any:
    """
    Serialize a value for JSON storage.

    Recursively converts Path, datetime, bytes, and nested structures
    to JSON-compatible types.
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [serialise_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): serialise_value(v) for k, v in value.items()}
    try:
        return str(value)
    except Exception:
        return None


def serialise_for_store(data: Any) -> Any:
    """
    Serialize a complete data structure for a Dash dcc.Store component.

    Wrapper around serialise_value for semantic clarity.
    """
    return serialise_value(data)


# ─────────────────────────────────────────────────────────────────────────────
# JSON File I/O
# ─────────────────────────────────────────────────────────────────────────────

def load_json_file(path: Path) -> Dict[str, Any]:
    """
    Load a JSON file and return its contents as a dictionary.

    Returns an empty dict if the file doesn't exist or fails to parse.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {}


def save_json_file(path: Path, data: Dict[str, Any]) -> None:
    """
    Save a dictionary to a JSON file, creating parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Schema / Config Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_schema(block_name: str) -> Dict[str, Any]:
    """Load a block schema from default_json/<block_name>_schema.json."""
    return load_json_file(SCHEMA_DIR / f"{block_name}_schema.json")


def load_config(block_name: str) -> Dict[str, Any]:
    """Load a block configuration from config_json/<block_name>.json."""
    return load_json_file(CONFIG_DIR / f"{block_name}.json")


def save_config(block_name: str, config: Dict[str, Any]) -> None:
    """Save a block configuration to config_json/<block_name>.json."""
    save_json_file(CONFIG_DIR / f"{block_name}.json", config)


# ─────────────────────────────────────────────────────────────────────────────
# Schema Utilities
# ─────────────────────────────────────────────────────────────────────────────

def get_field_ids_from_schema(schema: Dict[str, Any]) -> List[str]:
    """Extract all field IDs from a block schema."""
    field_ids = []
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            field_id = field.get("id")
            if field_id:
                field_ids.append(field_id)
    return field_ids


def extract_defaults_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Extract default values from a block schema."""
    defaults = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            field_id = field.get("id")
            default = field.get("default")
            path = field.get("path", field_id)
            if field_id and default is not None:
                defaults[path or field_id] = default
    return defaults


def extract_form_state(schema: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract form state from config based on schema field paths."""
    form_state = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            field_id = field.get("id")
            if not field_id:
                continue
            path = field.get("path", field_id)
            value = config
            if path:
                for key in path.split("."):
                    if isinstance(value, dict):
                        value = value.get(key)
                    else:
                        value = None
                        break
            form_state[field_id] = value if value is not None else field.get("default")
    return form_state


def apply_form_state(
    schema: Dict[str, Any],
    current_config: Dict[str, Any],
    form_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply form state values to a config dict based on schema field paths."""
    config = copy.deepcopy(current_config) if current_config else {}

    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            field_id = field.get("id")
            if not field_id or field_id not in form_state:
                continue
            path = field.get("path", field_id)
            value = form_state[field_id]

            if path:
                keys = path.split(".")
                target = config
                for key in keys[:-1]:
                    if key not in target or not isinstance(target[key], dict):
                        target[key] = {}
                    target = target[key]
                target[keys[-1]] = value
            else:
                config[field_id] = value

    return config


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Generic datetime
    "parse_iso_datetime",
    "now_utc",
    "format_timestamp",
    # Serialization
    "serialise_value",
    "serialise_for_store",
    # JSON I/O
    "load_json_file",
    "save_json_file",
    "load_schema",
    "load_config",
    "save_config",
    # Schema utilities
    "get_field_ids_from_schema",
    "extract_defaults_from_schema",
    "extract_form_state",
    "apply_form_state",
]
