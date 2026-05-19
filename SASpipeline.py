"""
SASpipeline.py
--------------
Public facade for the SAS pipeline. Tells the world *which* pipeline this is —
the names of the blocks, the order they run in — and exposes a tiny set of
helpers the UI uses to drive a singleton :class:`Pipeline` instance.

All the orchestration logic (threading, schema dispatch, paralelismo,
required_when, validación de outputs, manejo de artifacts) lives in:

    utils.executor.Executor   — schema-driven engine
    utils.pipeline.Pipeline   — engine + thread + PipelineState wiring

This file is intentionally small. If you find yourself adding lógica de
ejecución aquí, that lógica belongs to ``utils/pipeline.py`` instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules import MODULE_REGISTRY
from utils.io_helpers import (
    load_config as _load_config,
    load_schema as _load_schema,
)
from utils.pipeline import Pipeline, PipelineState
from utils.pipeline_configs import (
    PIPELINE_CONFIGS_DIR,
    apply_pipeline_config,
    list_saved_configs,
    load_pipeline_config,
)
from utils.pipeline_configs import save_current_config as _save_current_config_impl
from utils.schema_validation import schema_field_specs

# Re-exports the UI imports from this module (rule of the house: ui/ only
# imports from SASpipeline).
from utils import (  # noqa: F401
    CONFIG_DIR,
    SCHEMA_DIR,
    apply_form_state,
    get_field_ids_from_schema,
    load_json_file,
    save_json_file,
    save_config,
)
# Re-export commonly imported helpers that some UI/CLI code still references.
from utils.snapshot_naming import (  # noqa: F401
    extract_location_from_snapshot_filename,
    parse_scheduled_time_doy,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_JSON_DIR = BASE_DIR / "config_json"

# ─────────────────────────────────────────────────────────────────────────────
# Module identifiers and execution order
# ─────────────────────────────────────────────────────────────────────────────

MODULE_CONFIG_INIT    = "ConfigInit"
MODULE_TIME_SYNC      = "TimeReferenceSynchronizer"
MODULE_ALMANAC        = "AlmanacManagement"
MODULE_DOWNLOADER     = "BGDandRECSdownloader"
MODULE_PARSER         = "BGDandRECSparser"
MODULE_UNCERTAINTY    = "UncertaintyModule"
MODULE_SNAPSHOT       = "SnapshotRecording"
MODULE_TESLA          = "TESLAkeyManagement"
MODULE_RECS           = "RECSDecryption"
MODULE_SIGNAL_CORR    = "SignalCorrelation"
MODULE_EPHEMERIDES    = "EphemeridesManagement"
MODULE_AUTHENTICATOR  = "SatelliteAuthenticator"
MODULE_PVT            = "PVTComputation"

# SnapshotRecording runs BEFORE BGDandRECSdownloader so that in live mode the
# actual snapshot timestamp can be used as GST for the RECS request.
PIPELINE_ORDER: List[str] = [
    MODULE_CONFIG_INIT,
    MODULE_TIME_SYNC,
    MODULE_ALMANAC,
    MODULE_UNCERTAINTY,
    MODULE_SNAPSHOT,
    MODULE_DOWNLOADER,
    MODULE_PARSER,
    MODULE_TESLA,
    MODULE_RECS,
    MODULE_SIGNAL_CORR,
    MODULE_EPHEMERIDES,
    MODULE_AUTHENTICATOR,
    MODULE_PVT,
]

# Kept for backwards compatibility with ui/services/pipeline.py imports.
INDEPENDENT_MODULES: List[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# Module helpers used by the UI
# ─────────────────────────────────────────────────────────────────────────────

def get_module_run_pipeline(block_name: str):
    """Resolve the 1.0 ``run_pipeline`` callable for a block."""
    info = MODULE_REGISTRY.get(block_name)
    if not info:
        raise KeyError(f"Block '{block_name}' not found in registry")
    fn = info.get("run_pipeline")
    if not callable(fn):
        raise ValueError(f"Block '{block_name}' has no callable run_pipeline")
    return fn


def get_module_config(block_name: str) -> Dict[str, Any]:
    """Read the persisted config from ``config_json/<Name>.json``."""
    try:
        return _load_config(block_name)
    except Exception:
        return {}


def get_module_schema(block_name: str) -> Dict[str, Any]:
    """Read the schema from ``default_json/<Name>_schema.json``."""
    try:
        return _load_schema(block_name)
    except Exception:
        return {}


def get_module_default_config(block_name: str) -> Dict[str, Any]:
    """Extract default values for every editable field declared in the schema."""
    schema = get_module_schema(block_name)
    return {
        fid: spec["default"]
        for fid, spec in schema_field_specs(schema).items()
        if "default" in spec
    }


def load_modules() -> List[Dict[str, Any]]:
    """Build the block list the UI graph consumes. Display name comes from the
    block's own schema (``schema.block_name``)."""
    CONFIG_JSON_DIR.mkdir(exist_ok=True)
    blocks: List[Dict[str, Any]] = []
    for block_id in PIPELINE_ORDER:
        schema = get_module_schema(block_id)
        blocks.append({
            "id": block_id,
            "name": schema.get("block_name") or block_id,
            "status": "READY",
            "params": get_module_config(block_id),
            "meta": {"module": block_id},
        })
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline configuration management — re-exported from utils.pipeline_configs
# ─────────────────────────────────────────────────────────────────────────────

def save_current_config(name: Optional[str] = None) -> str:
    """Snapshot the current ``config_json/`` files into ``pipeline_configs/``."""
    return _save_current_config_impl(PIPELINE_ORDER, name=name)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton Pipeline + façade for the UI
# ─────────────────────────────────────────────────────────────────────────────

_PIPELINE_STATE = PipelineState()
_PIPELINE = Pipeline(
    registry=MODULE_REGISTRY,
    order=PIPELINE_ORDER,
    state=_PIPELINE_STATE,
    config_init_module=MODULE_CONFIG_INIT,
)


def get_pipeline() -> Pipeline:
    """Return the process-wide :class:`Pipeline` instance."""
    return _PIPELINE


def get_pipeline_state() -> PipelineState:
    """Return the process-wide :class:`PipelineState` (shared with the UI)."""
    return _PIPELINE_STATE


# Alias kept so old import sites keep working. Prefer ``get_pipeline()``.
PipelineRunner = Pipeline  # type: ignore[assignment]


def start_pipeline() -> None:
    """Spawn the pipeline in a daemon thread (UI Run button)."""
    _PIPELINE.run_async()


def stop_pipeline() -> None:
    """Cooperatively stop the pipeline (UI Stop button)."""
    _PIPELINE.stop()


def run_from_config(config_path: str, sync: bool = False) -> Optional[PipelineState]:
    """Apply a saved pipeline config and run it. ``sync=True`` blocks and
    returns the final state; ``sync=False`` (default) spawns a thread and
    returns ``None`` immediately."""
    return _PIPELINE.run_from_config(config_path, sync=sync)


def run_from_config_sync(config_path: str) -> PipelineState:
    """Synchronous variant kept for callers that hard-coded the name."""
    state = _PIPELINE.run_from_config(config_path, sync=True)
    assert state is not None
    return state


def run_single_module(
    block_id: str,
    params: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Any]:
    """UI single-block button: run one block via the Executor's standalone
    path. Returns ``(success, result_or_error_message)``."""
    try:
        result = _PIPELINE.run_block(block_id, params=params, context=context)
        return True, result
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, str(e)


__all__ = [
    # Constants
    "BASE_DIR",
    "CONFIG_JSON_DIR",
    "PIPELINE_ORDER",
    "INDEPENDENT_MODULES",
    "MODULE_CONFIG_INIT", "MODULE_TIME_SYNC", "MODULE_ALMANAC",
    "MODULE_DOWNLOADER", "MODULE_PARSER", "MODULE_UNCERTAINTY",
    "MODULE_SNAPSHOT", "MODULE_TESLA", "MODULE_RECS",
    "MODULE_SIGNAL_CORR", "MODULE_EPHEMERIDES", "MODULE_AUTHENTICATOR",
    "MODULE_PVT",
    # Module helpers
    "MODULE_REGISTRY",
    "get_module_run_pipeline",
    "get_module_config",
    "get_module_schema",
    "get_module_default_config",
    "load_modules",
    # Saved-config management
    "PIPELINE_CONFIGS_DIR",
    "save_current_config",
    "list_saved_configs",
    "load_pipeline_config",
    "apply_pipeline_config",
    # Pipeline driving
    "PipelineState",
    "PipelineRunner",
    "Pipeline",
    "get_pipeline",
    "get_pipeline_state",
    "start_pipeline",
    "stop_pipeline",
    "run_from_config",
    "run_from_config_sync",
    "run_single_module",
]
