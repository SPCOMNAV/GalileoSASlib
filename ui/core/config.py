"""
UI Configuration
----------------
Constants and configuration for the UI. No internal dependencies.
"""

from pathlib import Path

# -------------------------------------------------------------------------
#  PATHS
# -------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # SASlib/
CONFIG_JSON_DIR = BASE_DIR / "config_json"
RESULTS_DIR = BASE_DIR / "results"

# -------------------------------------------------------------------------
#  MODULE VISUAL STYLING
# -------------------------------------------------------------------------
MODULE_MIN_WIDTH = 320
MODULE_MAX_WIDTH = 460
MODULE_BORDER_COLOR = "#60a5fa"
MODULE_BODY_BG = "#ffffff"
MODULE_TEXT_COLOR = "#0f172a"
MODULE_BOX_SHADOW = "0 16px 34px rgba(59, 130, 246, 0.14)"
MODULE_BORDER_RADIUS = 0
MODULE_HEADER_HEIGHT = 64
MODULE_HEADER_BG = "#bfdbfe"

# -------------------------------------------------------------------------
#  STATUS COLORS
# -------------------------------------------------------------------------
STATUS_COLORS = {
    "idle": "#9ca3af",
    "running": "#3b82f6",
    "success": "#22c55e",
    "error": "#ef4444",
    "warning": "#f59e0b",
    "unknown": "#6b7280",
}

# -------------------------------------------------------------------------
#  INTERVALS (milliseconds)
# -------------------------------------------------------------------------
LOG_UPDATE_INTERVAL_MS = 2000
CLICK_DETECTOR_INTERVAL_MS = 150

# -------------------------------------------------------------------------
#  REACTFLOW GRAPH SETTINGS
# -------------------------------------------------------------------------
GRAPH_STYLE = {
    "height": "420px",
    "border": "1px solid #e5e7eb",
    "borderRadius": "8px",
}
