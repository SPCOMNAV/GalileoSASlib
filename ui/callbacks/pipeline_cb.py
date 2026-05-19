"""
Pipeline Callbacks
------------------
Callbacks for pipeline start/stop and configuration import.
"""

import base64
import copy
import json
from typing import Any

from dash import Dash, Input, Output, State, callback_context, no_update

from ui.core.state import log_manager
from ui.services.pipeline import pipeline_service
from ui.services.persistence import config_persistence


def register_callbacks(app: Dash) -> None:
    """Register pipeline-related callbacks."""
    
    @app.callback(
        Output("pipeline-alert", "children"),
        Output("pipeline-alert", "color"),
        Output("pipeline-alert", "hide"),
        Output("pipeline-alert", "title"),
        Output("pipeline-status-text", "children"),
        Input("start-pipeline-btn", "n_clicks"),
        Input("stop-pipeline-btn", "n_clicks"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def handle_pipeline_buttons(n_start, n_stop, blocks_data):
        """Handle pipeline start/stop button clicks."""
        ctx = callback_context
        if not ctx.triggered:
            return "", "blue", True, "", ""
        
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        
        if trigger_id == "start-pipeline-btn":
            result = pipeline_service.start()
            if result["success"]:
                return (result["message"], "green", False, "Pipeline", "Running...")
            else:
                return (result["error"], "red", False, "Error", "Error")
        
        if trigger_id == "stop-pipeline-btn":
            result = pipeline_service.stop()
            if result["success"]:
                return (result["message"], "yellow", False, "Pipeline", "Stopped.")
            else:
                return (result["error"], "red", False, "Error", "Error")
        
        return "", "blue", True, "", ""
    
    @app.callback(
        Output("pipeline-alert", "children", allow_duplicate=True),
        Output("pipeline-alert", "color", allow_duplicate=True),
        Output("pipeline-alert", "hide", allow_duplicate=True),
        Output("pipeline-alert", "title", allow_duplicate=True),
        Output("blocks-store", "data", allow_duplicate=True),
        Input("import-config-upload", "contents"),
        State("import-config-upload", "filename"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def handle_import_config(contents, filename, blocks_data):
        """Handle importing a pipeline configuration from a JSON file."""
        if contents is None:
            return no_update, no_update, no_update, no_update, no_update
        
        try:
            # Parse the uploaded file content
            content_type, content_string = contents.split(',')
            decoded = base64.b64decode(content_string).decode('utf-8')
            config_data = json.loads(decoded)
            
            log_manager.append(f"Importing configuration from {filename}")
            
            # Apply config using the unified pipeline_service
            result = pipeline_service.apply_config(config_data)
            
            if not result["success"]:
                return (result["error"], "red", False, "Import Error", no_update)
            
            # Update blocks_store with imported configuration for UI display.
            # Accept both the new "module_configs" key and the legacy
            # "block_configs" key used in older saved configs.
            updated_blocks = copy.deepcopy(blocks_data) if blocks_data else []
            module_configs = (
                config_data.get("module_configs")
                or config_data.get("block_configs", {})
            )
            
            for block in updated_blocks:
                block_id = block.get("id")
                if block_id in module_configs:
                    imported_config = module_configs[block_id]
                    if "params" not in block:
                        block["params"] = {}
                    block["params"].update(imported_config)
            
            log_manager.append(f"Configuration imported successfully from {filename}")
            
            return (
                f"Configuration imported from {filename}",
                "green",
                False,
                "Import Success",
                updated_blocks,
            )
            
        except json.JSONDecodeError as e:
            log_manager.append(f"Error parsing JSON file: {e}")
            return (f"Invalid JSON file: {e}", "red", False, "Import Error", no_update)
        except Exception as e:
            log_manager.append(f"Error importing configuration: {e}")
            return (f"Error importing configuration: {e}", "red", False, "Import Error", no_update)
