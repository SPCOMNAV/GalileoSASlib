"""
UI Components Module
--------------------
Visual components for the UI. Depends on core/ and services/.
"""

from .graph import make_nodes, make_edges, node_label, MODULE_ORDER, EDGES
from .modals import create_details_modal, create_json_modal
from .plots import build_signal_corr_plot, build_authenticator_plot, build_almanac_skyplot
from .forms import (
    build_form_from_schema,
    build_result_display,
    build_generic_layout,
    set_config_init_params,
    register_generic_callbacks,
    VALUE_STYLE,
    LABEL_STYLE,
)

__all__ = [
    # Graph
    "make_nodes", "make_edges", "node_label", "MODULE_ORDER", "EDGES",
    # Modals
    "create_details_modal", "create_json_modal",
    # Plots
    "build_signal_corr_plot", "build_authenticator_plot", "build_almanac_skyplot",
    # Forms
    "build_form_from_schema", "build_result_display", "build_generic_layout",
    "set_config_init_params", "register_generic_callbacks",
    "VALUE_STYLE", "LABEL_STYLE",
]
