"""
UI Core Module
--------------
Base layer with no internal dependencies.
Contains types, state management, and configuration.
"""

from .types import ClickData, PipelineResult, ModalState
from .state import ResultsCache, LogManager, results_cache, log_manager
from .config import (
    # Module styling
    MODULE_MIN_WIDTH, MODULE_MAX_WIDTH, MODULE_BORDER_COLOR, MODULE_BODY_BG,
    MODULE_TEXT_COLOR, MODULE_BOX_SHADOW, MODULE_BORDER_RADIUS,
    MODULE_HEADER_HEIGHT, MODULE_HEADER_BG,
    # Paths
    BASE_DIR, CONFIG_JSON_DIR, RESULTS_DIR,
    # Intervals
    LOG_UPDATE_INTERVAL_MS, CLICK_DETECTOR_INTERVAL_MS,
    # Graph
    GRAPH_STYLE,
)

__all__ = [
    # Types
    "ClickData", "PipelineResult", "ModalState",
    # State
    "ResultsCache", "LogManager", "results_cache", "log_manager",
    # Config
    "MODULE_MIN_WIDTH", "MODULE_MAX_WIDTH", "MODULE_BORDER_COLOR", "MODULE_BODY_BG",
    "MODULE_TEXT_COLOR", "MODULE_BOX_SHADOW", "MODULE_BORDER_RADIUS",
    "MODULE_HEADER_HEIGHT", "MODULE_HEADER_BG",
    "BASE_DIR", "CONFIG_JSON_DIR", "RESULTS_DIR",
    "LOG_UPDATE_INTERVAL_MS", "CLICK_DETECTOR_INTERVAL_MS",
    "GRAPH_STYLE",
]
