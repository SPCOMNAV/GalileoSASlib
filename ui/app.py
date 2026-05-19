"""
SAS Dashboard UI - Main Application
-----------------------------------
Dash application for the SAS pipeline interface.

Usage:
    cd SASlib && python -m ui.app
    # or
    from ui import app, server
"""

import sys
from pathlib import Path

# Allow running directly: python ui/app.py
if __name__ == "__main__" and __package__ is None:
    # Add parent directory to path so imports work
    _parent = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_parent))
    __package__ = "ui"

import dash
import dash._dash_renderer
dash._dash_renderer._set_react_version("18.2.0")

from dash import html, dcc
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
from dash_diagram import Reactflow as ReactFlow

# Core imports
from ui.core.config import LOG_UPDATE_INTERVAL_MS, CLICK_DETECTOR_INTERVAL_MS, GRAPH_STYLE

# Services imports
from ui.services.pipeline import pipeline_service

# Components imports
from ui.components.graph import make_nodes, make_edges
from ui.components.modals import create_details_modal, create_json_modal, create_wait_modal, create_ntp_selector_modal, create_schedule_picker_modal

# Callbacks registration
from ui.callbacks import register_all_callbacks


################################################################################### DASH APP ############################################
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
)
server = app.server

# Hide ReactFlow minimap via CSS
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            /* Hide ReactFlow minimap */
            .react-flow__minimap {
                display: none !important;
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
'''


############################################################## LAYOUT ################################################################
def create_layout():
    """Create the main application layout."""
    initial_blocks_data = pipeline_service.load_modules()
    
    return dmc.MantineProvider(
        theme={"colorScheme": "light"},
        children=[
            dbc.Container([
                # State stores
                dcc.Store(id="blocks-store", data=initial_blocks_data),
                dcc.Store(id="selected-block-id"),
                dcc.Store(id="click-with-timestamp", data=None),
                dcc.Store(id="recs-parser-store"),
                dcc.Store(id="snapshot-latest-path"),
                dcc.Store(id="snapshot-analysis-store"),
                dcc.Store(id="recs-decrypt-store"),
                dcc.Store(id="signal-corr-store"),
                dcc.Store(id="authenticator-store"),
                dcc.Store(id="time-sync-store"),
                dcc.Store(id="almanac-store"),
                dcc.Store(id="ephemerides-store"),
                dcc.Store(id="pvt-store"),
                dcc.Store(id="snapshot-wait-state", data={"active": False, "message": ""}),
                dcc.Download(id="config-json-download"),
                
                # Intervals
                dcc.Interval(id="log-tick", interval=LOG_UPDATE_INTERVAL_MS, n_intervals=0), # Update logs from the pipeline (backend) periodically
                dcc.Interval(id="click-detector", interval=CLICK_DETECTOR_INTERVAL_MS, n_intervals=0), # Detect clicks on the ReactFlow graph
                
                # Header
                html.H3("SASlib Graphic Interface"),
                html.P("Click on a block to inspect details. Edit the JSON / forms to update parameters."),
                
                # Pipeline graph
                ReactFlow(
                    id="rf",
                    nodes=make_nodes(initial_blocks_data),
                    edges=make_edges(),
                    fit_view=True,
                    style=GRAPH_STYLE,
                ),
                
                html.Hr(),
                
                # Pipeline controls
                dbc.Row([
                    dbc.Col([
                        dbc.Button("Start pipeline", id="start-pipeline-btn", color="primary", className="me-2"),
                        dbc.Button("Stop pipeline", id="stop-pipeline-btn", color="danger", className="me-2"),
                        html.Div(
                            dcc.Upload(
                                id="import-config-upload",
                                children=dbc.Button("Import Config", id="import-config-btn", color="secondary", className="me-2"),
                                accept=".json",
                                max_size=10 * 1024 * 1024,
                            ),
                            style={"display": "inline-block"},
                        ),
                        html.Span(id="pipeline-status-text", className="ms-2"),
                    ], width="auto"),
                    dbc.Col(
                        dmc.Alert(id="pipeline-alert", hide=True, duration=4000, title="", children=""),
                        width=True,
                    ),
                ], className="mb-3"),
                
                # Terminal/Logs
                dmc.Paper([
                    html.H5("Terminal", className="mb-2"),
                    html.Pre(
                        id="logs-output",
                        style={
                            "backgroundColor": "#1e1e1e",
                            "color": "#d4d4d4",
                            "padding": "12px",
                            "borderRadius": "4px",
                            "maxHeight": "200px",
                            "overflowY": "auto",
                            "fontFamily": "monospace",
                            "fontSize": "13px",
                        },
                    ),
                ], p="md", shadow="sm", className="mb-4"),
                
                # Debug area (hidden in production)
                html.Div(id="debug-click", style={"display": "none"}),
                
                # Modals
                create_details_modal(),
                create_json_modal(),
                create_wait_modal(),
                create_ntp_selector_modal(),
                create_schedule_picker_modal(),
                
            ], fluid=True),
        ],
    )


# Set the layout
app.layout = create_layout()

# Register all callbacks
register_all_callbacks(app)


############################################ MAIN RUNNER ############################################
def run(debug: bool = True, host: str = "127.0.0.1", port: int = 8050):
    """Run the application."""
    app.run(debug=debug, host=host, port=port)


if __name__ == "__main__":
    run()
