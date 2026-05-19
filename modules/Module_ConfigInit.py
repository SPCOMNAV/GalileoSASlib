from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from utils.utils import (
    load_schema as _load_schema,
    load_config as _load_config,
    save_config as _save_config,
)
from utils.schema_validation import validate_against_schema

################################################ CONSTANTS ################################################

MODULE_FILE = "ConfigInit"

# Valid auxiliary modes (used as a small post-validation guard since the
# schema does not declare an enum constraint).
VALID_AUXILIARY_MODES = ["off", "E1B", "E1C", "E5B", "E5C", "E6B"]

################################################# SCHEMA / CONFIG LOADERS #################################################

def load_schema() -> Dict[str, Any]:
    return _load_schema(MODULE_FILE)


def load_config() -> Dict[str, Any]:
    config = _load_config(MODULE_FILE)
    return config if config else get_defaults()


def save_config(config: Dict[str, Any]) -> None:
    _save_config(MODULE_FILE, config)


def get_defaults() -> Dict[str, Any]:
    """Extract default values from schema."""
    schema = load_schema()
    defaults = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            field_id = field.get("id")
            if field_id and "default" in field:
                defaults[field_id] = field["default"]
    return defaults


################################################# Configuration validation ##############################################################

def validate_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Coerce every field declared in the schema. Adding a field to the schema
    is enough — no code change here is needed.

    A tiny post-validation guard pins ``auxiliary_mode`` to one of the values
    accepted by the rest of the codebase (the schema declares it as a string
    without an explicit enum constraint)."""
    validated = validate_against_schema(config, load_schema())
    if validated.get("auxiliary_mode") not in VALID_AUXILIARY_MODES:
        validated["auxiliary_mode"] = "off"
    return validated


################################################# Pipeline execution ##############################################################

def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """
    Validate the global ConfigInit fields and publish them to the shared
    context so every other block can consume them as inputs.

    Contract 1.0:
        consumes: nothing
        produces: every key listed in ConfigInit_schema.json `produces`
    """
    if config is None:
        config = load_config()

    validated = validate_config(config)
    save_config(validated)

    return {
        "outputs": dict(validated),
        "summary": {
            "status": "success",
            "message": "Configuration validated and published to context",
            "field_count": len(validated),
        },
        "data": {"config": validated},
    }


run_pipeline.__contract_version__ = "1.0"


######################################################## EXPORTS ########################################################

__all__ = [
    "load_schema",
    "load_config",
    "save_config",
    "get_defaults",
    "validate_config",
    "run_pipeline",
    "VALID_AUXILIARY_MODES",
]

