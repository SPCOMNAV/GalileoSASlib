"""
utils/paths.py
--------------
Path constants, results-directory helpers, and logging utilities
shared by all pipeline blocks.
"""

from __future__ import annotations

import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Common Paths
# ─────────────────────────────────────────────────────────────────────────────

BLOCKS_UTILS_DIR = Path(__file__).resolve().parent
BASE_DIR = BLOCKS_UTILS_DIR.parent
SCHEMA_DIR = BASE_DIR / "default_json"
CONFIG_DIR = BASE_DIR / "config_json"

# Dataset configuration - change DATASET_NAME to switch datasets
DATASET_NAME = "datasetION"
DATA_DIR = BASE_DIR / "data" / DATASET_NAME
RESULTS_DIR = BASE_DIR / "results" / DATASET_NAME


def get_results_subdir(subdir: str) -> Path:
    """
    Get a subdirectory inside the results folder, creating it if needed.

    Args:
        subdir: Name of the subdirectory (e.g., 'ECS', 'plots', 'logs').

    Returns:
        Path to the subdirectory.
    """
    path = RESULTS_DIR / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def clear_results_subdir(subdir: str) -> int:
    """
    Clear all files in a results subdirectory.

    Args:
        subdir: Name of the subdirectory (e.g., 'ECS').

    Returns:
        Number of files/dirs deleted.
    """
    import shutil
    path = RESULTS_DIR / subdir
    count = 0
    if path.exists():
        for item in path.iterdir():
            if item.is_file():
                item.unlink()
                count += 1
            elif item.is_dir():
                shutil.rmtree(item)
                count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Logging / Debug
# ─────────────────────────────────────────────────────────────────────────────

# Set to False to suppress debug output
DEBUG_MODE = True


def debug(block_name: str, msg: str) -> None:
    """Print a debug message with block name prefix."""
    print(f"[{block_name}] {msg}")


def console_log(tag: str, msg: str, to_ui: bool = True) -> None:
    """
    Print a tagged console message and optionally send to UI terminal.

    Args:
        tag: Tag/prefix for the message.
        msg: The message to print.
        to_ui: If True, also send to PipelineState for UI display.
    """
    formatted = f"[{tag}] {msg}"
    print(formatted)

    if to_ui:
        try:
            from SASpipeline import get_pipeline_state
            state = get_pipeline_state()
            if state.is_running():
                state.append_log_raw(formatted)
        except Exception:
            pass


def debug_log(tag: str, msg: str) -> None:
    """
    Print a debug message (only when DEBUG_MODE is True).

    Args:
        tag: Tag/prefix for the message.
        msg: The debug message to print.
    """
    if DEBUG_MODE:
        formatted = f"[{tag}] {msg}"
        print(formatted)
        try:
            from SASpipeline import get_pipeline_state
            state = get_pipeline_state()
            if state.is_running():
                state.append_log_raw(formatted)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "BLOCKS_UTILS_DIR",
    "BASE_DIR",
    "SCHEMA_DIR",
    "CONFIG_DIR",
    "DATASET_NAME",
    "DATA_DIR",
    "RESULTS_DIR",
    "get_results_subdir",
    "clear_results_subdir",
    "DEBUG_MODE",
    "debug",
    "console_log",
    "debug_log",
]
