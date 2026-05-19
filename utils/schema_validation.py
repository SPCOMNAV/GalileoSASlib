"""
schema_validation.py
--------------------
Schema-driven type coercion and validation.

Each block schema declares its editable fields under ``sections[].fields[]`` with
an ``id``, ``type`` (string|int|float|bool|list), ``default`` and optional
``props.min/max``. Instead of hand-writing a ``validate_config`` per block that
duplicates this information, blocks call :func:`validate_against_schema` which
walks the schema and applies the right coercion to every declared field.

Adding a new field to a block then becomes a one-step change: edit the schema.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from utils.coerce import (
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_list,
    coerce_string,
)


def schema_field_specs(schema: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Flatten a schema's ``sections[].fields[]`` into ``{field_id: spec}``."""
    specs: Dict[str, Dict[str, Any]] = {}
    for section in schema.get("sections", []) or []:
        for field in section.get("fields", []) or []:
            fid = field.get("id")
            if fid:
                specs[fid] = field
    return specs


def schema_field_ids(schema: Mapping[str, Any]) -> List[str]:
    """Field IDs in declaration order. Useful for auto-deriving ``produces``."""
    return list(schema_field_specs(schema).keys())


def coerce_field(value: Any, spec: Mapping[str, Any]) -> Any:
    """Coerce a single value according to its schema field spec.

    Honours ``type``, ``default`` and ``props.min/max``. Unknown types fall back
    to string coercion so we never raise on an unfamiliar declaration.
    """
    ftype = (spec.get("type") or "string").lower()
    default = spec.get("default")
    props = spec.get("props") or {}
    minimum = props.get("min")
    maximum = props.get("max")

    if ftype in ("float", "number"):
        return coerce_float(
            value,
            default if default is not None else 0.0,
            minimum=minimum,
            maximum=maximum,
        )
    if ftype in ("int", "integer"):
        return coerce_int(
            value,
            default if default is not None else 0,
            minimum=minimum,
            maximum=maximum,
        )
    if ftype in ("bool", "boolean"):
        return coerce_bool(value, default if default is not None else False)
    if ftype == "list":
        return coerce_list(value, default if default is not None else [])
    return coerce_string(value, default if default is not None else "")


def validate_against_schema(
    config: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a fully-coerced config dict from a raw config and its schema.

    Every field declared under ``schema.sections[].fields[]`` ends up in the
    returned dict, with type coercion and default fallback applied. Fields
    present in ``config`` but absent from the schema are silently dropped (the
    schema is the contract).
    """
    out: Dict[str, Any] = {}
    for fid, spec in schema_field_specs(schema).items():
        out[fid] = coerce_field(config.get(fid), spec)
    return out


__all__ = [
    "schema_field_specs",
    "schema_field_ids",
    "coerce_field",
    "validate_against_schema",
]
