"""
ReactFlow Graph Components
--------------------------
Components for building the pipeline visualization graph.
Depends on core/ and services/.
"""

from typing import List, Dict, Any

from ui.core.config import (
    MODULE_MIN_WIDTH, MODULE_MAX_WIDTH, MODULE_BORDER_COLOR, MODULE_BODY_BG,
    MODULE_TEXT_COLOR, MODULE_BOX_SHADOW, MODULE_BORDER_RADIUS,
    MODULE_HEADER_HEIGHT, MODULE_HEADER_BG, STATUS_COLORS,
)
from ui.services.pipeline import pipeline_service
from ui.services.serializers import preview_param_value


# -------------------------------------------------------------------------
#  MODULE POSITIONS - Order of modules for layout calculation
# -------------------------------------------------------------------------

# Independent modules (shown above main pipeline, not connected)
INDEPENDENT_MODULE_ORDER = []

# Main pipeline modules (connected in sequence)
MODULE_ORDER = [
    pipeline_service.MODULE_CONFIG_INIT,
    pipeline_service.MODULE_TIME_SYNC,
    pipeline_service.MODULE_ALMANAC,
    pipeline_service.MODULE_UNCERTAINTY,
    pipeline_service.MODULE_SNAPSHOT,
    pipeline_service.MODULE_DOWNLOADER,
    pipeline_service.MODULE_PARSER,
    pipeline_service.MODULE_TESLA,
    pipeline_service.MODULE_RECS,
    pipeline_service.MODULE_SIGNAL_CORR,
    pipeline_service.MODULE_EPHEMERIDES,
    pipeline_service.MODULE_AUTHENTICATOR,
    pipeline_service.MODULE_PVT,
]

# Gap between modules (pixels)
MODULE_GAP = 40
# Starting X position
START_X = 50
# Starting X position for independent modules (more to the right)
INDEPENDENT_START_X = 350
# Y position for independent modules (top row)
INDEPENDENT_Y = -450
# Y position for main pipeline modules (second row)
MODULE_Y = 50

# -------------------------------------------------------------------------
#  STATIC EDGES
# -------------------------------------------------------------------------
EDGES = [
    {"source": pipeline_service.MODULE_CONFIG_INIT, "target": pipeline_service.MODULE_TIME_SYNC},
    {"source": pipeline_service.MODULE_TIME_SYNC, "target": pipeline_service.MODULE_ALMANAC},
    {"source": pipeline_service.MODULE_ALMANAC, "target": pipeline_service.MODULE_UNCERTAINTY},
    {"source": pipeline_service.MODULE_UNCERTAINTY, "target": pipeline_service.MODULE_SNAPSHOT},
    {"source": pipeline_service.MODULE_SNAPSHOT, "target": pipeline_service.MODULE_DOWNLOADER},
    {"source": pipeline_service.MODULE_DOWNLOADER, "target": pipeline_service.MODULE_PARSER},
    {"source": pipeline_service.MODULE_PARSER, "target": pipeline_service.MODULE_TESLA},
    {"source": pipeline_service.MODULE_TESLA, "target": pipeline_service.MODULE_RECS},
    {"source": pipeline_service.MODULE_RECS, "target": pipeline_service.MODULE_SIGNAL_CORR},
    {"source": pipeline_service.MODULE_SIGNAL_CORR, "target": pipeline_service.MODULE_EPHEMERIDES},
    {"source": pipeline_service.MODULE_EPHEMERIDES, "target": pipeline_service.MODULE_AUTHENTICATOR},
    {"source": pipeline_service.MODULE_AUTHENTICATOR, "target": pipeline_service.MODULE_PVT},
]


def node_label(block: Dict[str, Any]) -> str:
    """
    Generate multi-line text for a ReactFlow node.

    Args:
        block: Module data dict with 'name', 'status', 'params'

    Returns:
        Formatted string for node label
    """
    title = f"{block['name'].upper()}"

    # Status with visual indicator
    status = block.get('status', 'UNKNOWN').upper()
    status_icons = {
        "RUNNING": "⏳",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "CONFIGURED": "⚙️",
        "READY": "⬜",
    }
    icon = status_icons.get(status, "")
    subtitle = f"STATUS: {status} {icon}"

    lines = [title, subtitle, ""]

    params = block.get("params", {}) or {}
    if isinstance(params, dict):
        for key, value in params.items():
            lines.append(f"{key}: {preview_param_value(value)}")

    return "\n".join(lines)


def _build_node_style(status: str = "READY") -> Dict[str, Any]:
    """Build the CSS style for a node, adapting colors based on status."""
    status_upper = status.upper()

    # Choose border and header colors based on status
    if status_upper == "RUNNING":
        border_color = STATUS_COLORS["running"]  # blue
        header_bg = "#93c5fd"  # lighter blue
        box_shadow = f"0 0 20px rgba(59, 130, 246, 0.5), {MODULE_BOX_SHADOW}"
    elif status_upper == "SUCCESS":
        border_color = STATUS_COLORS["success"]  # green
        header_bg = "#bbf7d0"  # light green
        box_shadow = MODULE_BOX_SHADOW
    elif status_upper == "ERROR":
        border_color = STATUS_COLORS["error"]  # red
        header_bg = "#fecaca"  # light red
        box_shadow = MODULE_BOX_SHADOW
    elif status_upper == "CONFIGURED":
        border_color = STATUS_COLORS["warning"]  # amber
        header_bg = "#fef3c7"  # light amber
        box_shadow = MODULE_BOX_SHADOW
    else:
        border_color = MODULE_BORDER_COLOR
        header_bg = MODULE_HEADER_BG
        box_shadow = MODULE_BOX_SHADOW

    return {
        "width": "fit-content",
        "minWidth": f"{MODULE_MIN_WIDTH}px",
        "maxWidth": f"{MODULE_MAX_WIDTH}px",
        "height": "auto",
        "fontSize": 16,
        "fontWeight": 700,
        "whiteSpace": "pre-wrap",
        "padding": "16px 24px 20px 24px",
        "lineHeight": "1.45",
        "background": (
            f"linear-gradient(180deg, {header_bg} 0px, {header_bg} {MODULE_HEADER_HEIGHT}px, "
            f"{MODULE_BODY_BG} {MODULE_HEADER_HEIGHT}px, {MODULE_BODY_BG} 100%)"
        ),
        "border": f"2px solid {border_color}",
        "borderRadius": f"{MODULE_BORDER_RADIUS}px",
        "boxShadow": box_shadow,
        "color": MODULE_TEXT_COLOR,
        "textTransform": "none",
        "display": "inline-flex",
        "alignItems": "flex-start",
        "backgroundSize": f"100% {MODULE_HEADER_HEIGHT}px, auto",
        "backgroundPosition": "top left, center",
        "backgroundRepeat": "no-repeat, no-repeat",
    }


def _estimate_module_width(block: Dict[str, Any]) -> int:
    """
    Estimate the width of a module based on its content.

    Args:
        block: Module data dict

    Returns:
        Estimated width in pixels
    """
    # Get the label text
    label = node_label(block)
    lines = label.split("\n")

    # Find the longest line
    max_chars = max(len(line) for line in lines) if lines else 0

    # Estimate width: ~8 pixels per character + padding
    estimated_width = max_chars * 8 + 48  # 48px for padding

    # Clamp to min/max
    return max(MODULE_MIN_WIDTH, min(MODULE_MAX_WIDTH, estimated_width))


def make_nodes(modules_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generate ReactFlow nodes from module data with dynamic positioning.

    Args:
        modules_data: List of module data dicts

    Returns:
        List of ReactFlow node dicts
    """
    nodes: List[Dict[str, Any]] = []

    # Create a lookup for module data by ID
    modules_by_id = {m["id"]: m for m in modules_data}

    module_positions = {}

    # Calculate positions for independent modules (top row, starting more to the right)
    current_x = INDEPENDENT_START_X
    for module_id in INDEPENDENT_MODULE_ORDER:
        module = modules_by_id.get(module_id)
        if module:
            module_positions[module_id] = {"x": current_x, "y": INDEPENDENT_Y}
            width = _estimate_module_width(module)
            current_x += width + MODULE_GAP

    # Calculate positions for main pipeline modules (second row)
    current_x = START_X
    for module_id in MODULE_ORDER:
        module = modules_by_id.get(module_id)
        if module:
            # Store position for this module
            module_positions[module_id] = {"x": current_x, "y": MODULE_Y}

            # Calculate width and move to next position
            width = _estimate_module_width(module)
            current_x += width + MODULE_GAP

    # Create nodes with calculated positions
    for module in modules_data:
        module_id = module["id"]
        status = module.get("status", "READY")

        nodes.append({
            "id": module_id,
            "type": "default",
            "data": {"label": node_label(module)},
            "position": module_positions.get(module_id, {"x": 80, "y": 80}),
            "style": _build_node_style(status),
        })

    return nodes


def make_edges() -> List[Dict[str, Any]]:
    """
    Get the static pipeline edges.

    Returns:
        List of edge dicts
    """
    return EDGES
