"""
pipeline_configs.py
-------------------
Saved-pipeline-config management: list, load, apply and save the JSON
snapshots that live in ``pipeline_configs/`` and capture every block's
configuration plus run metadata.

Extracted out of SASpipeline.py to keep that file focused on pipeline
execution.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.paths import BASE_DIR, console_log


PIPELINE_CONFIGS_DIR = BASE_DIR / "pipeline_configs"
CONFIG_JSON_DIR = BASE_DIR / "config_json"


def _safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in text)


def list_saved_configs() -> List[Dict[str, Any]]:
    """Return summaries of every saved pipeline config (newest first)."""
    PIPELINE_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

    configs: List[Dict[str, Any]] = []
    for path in sorted(PIPELINE_CONFIGS_DIR.glob("*.json"), reverse=True):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = data.get("metadata", {})
            configs.append({
                "path": str(path),
                "filename": path.name,
                "execution_id": metadata.get("execution_id"),
                "execution_name": metadata.get("execution_name"),
                "created_at": metadata.get("created_at", "Unknown"),
                "version": metadata.get("version", "1.0"),
            })
        except Exception as e:
            configs.append({
                "path": str(path),
                "filename": path.name,
                "error": str(e),
            })
    return configs


def load_pipeline_config(config_path: str) -> Dict[str, Any]:
    """Load a saved pipeline configuration. Relative paths resolve under
    ``pipeline_configs/``."""
    path = Path(config_path)
    if not path.is_absolute():
        path = PIPELINE_CONFIGS_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"Pipeline config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def apply_pipeline_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Write each block config from a loaded pipeline config back into
    ``config_json/<block>.json``. Accepts both the new key ``module_configs``
    and the legacy key ``block_configs``."""
    module_configs = config.get("module_configs") or config.get("block_configs", {})
    applied: List[str] = []
    errors: List[Dict[str, str]] = []

    for block_name, block_config in module_configs.items():
        try:
            config_path = CONFIG_JSON_DIR / f"{block_name}.json"
            with config_path.open("w", encoding="utf-8") as f:
                json.dump(block_config, f, indent=2, ensure_ascii=False)
            applied.append(block_name)
        except Exception as e:
            errors.append({"block": block_name, "error": str(e)})

    return {
        "status": "success" if not errors else "partial",
        "applied_modules": applied,
        "errors": errors if errors else None,
    }


def save_current_config(
    pipeline_order: List[str],
    name: Optional[str] = None,
) -> str:
    """Snapshot the current ``config_json/<block>.json`` files into a single
    pipeline config under ``pipeline_configs/``. Returns the saved path."""
    PIPELINE_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

    execution_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    full_config: Dict[str, Any] = {
        "metadata": {
            "execution_id": execution_id,
            "execution_name": name if name else None,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "version": "1.0",
        },
        "module_configs": {},
    }

    for block_name in pipeline_order:
        cfg_path = CONFIG_JSON_DIR / f"{block_name}.json"
        if cfg_path.exists():
            try:
                with cfg_path.open("r", encoding="utf-8") as f:
                    full_config["module_configs"][block_name] = json.load(f)
            except Exception:
                full_config["module_configs"][block_name] = {}
        else:
            full_config["module_configs"][block_name] = {}

    if name:
        filename = f"{_safe_name(name)}.json"
    else:
        filename = f"pipeline_config_{execution_id}.json"

    filepath = PIPELINE_CONFIGS_DIR / filename
    with filepath.open("w", encoding="utf-8") as f:
        json.dump(full_config, f, indent=2, ensure_ascii=False, default=str)

    console_log("PipelineConfigs", f"Configuration saved to: {filepath}")
    return str(filepath)


def write_execution_config(
    execution_id: str,
    pipeline_order: List[str],
    execution_name: str = "",
    output_dir: Optional[Path] = None,
    also_save_to_pipeline_configs: bool = False,
    config_output_dir: str = "pipeline_configs",
) -> Dict[str, Path]:
    """Persist the running pipeline's configuration. Always writes
    ``executions/<execution_id>/execution.json``. Optionally also writes a
    second copy under ``config_output_dir`` (used as a "save snapshot on run"
    feature)."""
    full_config: Dict[str, Any] = {
        "metadata": {
            "execution_id": execution_id,
            "execution_name": execution_name or None,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "version": "1.0",
        },
        "module_configs": {},
    }

    for block_name in pipeline_order:
        cfg_path = CONFIG_JSON_DIR / f"{block_name}.json"
        if cfg_path.exists():
            try:
                with cfg_path.open("r", encoding="utf-8") as f:
                    full_config["module_configs"][block_name] = json.load(f)
            except Exception:
                full_config["module_configs"][block_name] = {}

    if output_dir is None:
        output_dir = BASE_DIR / "executions" / execution_id
    output_dir.mkdir(parents=True, exist_ok=True)
    exec_path = output_dir / "execution.json"
    with exec_path.open("w", encoding="utf-8") as f:
        json.dump(full_config, f, indent=2, ensure_ascii=False, default=str)
    written = {"execution": exec_path}

    if also_save_to_pipeline_configs:
        cfg_dir = Path(config_output_dir)
        if not cfg_dir.is_absolute():
            cfg_dir = BASE_DIR / cfg_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        if execution_name:
            filename = f"{_safe_name(execution_name)}.json"
        else:
            filename = f"pipeline_config_{execution_id}.json"
        snap_path = cfg_dir / filename
        with snap_path.open("w", encoding="utf-8") as f:
            json.dump(full_config, f, indent=2, ensure_ascii=False, default=str)
        written["snapshot"] = snap_path

    return written


__all__ = [
    "PIPELINE_CONFIGS_DIR",
    "CONFIG_JSON_DIR",
    "list_saved_configs",
    "load_pipeline_config",
    "apply_pipeline_config",
    "save_current_config",
    "write_execution_config",
]
