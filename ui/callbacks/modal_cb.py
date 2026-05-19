"""
Modal Callbacks
---------------
Callbacks for modal dialogs (details, JSON editor).
"""

import json
import traceback
from typing import Any

from dash import Dash, Input, Output, State, callback_context, no_update, html, ALL
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from ui.core.state import log_manager
from ui.services.pipeline import pipeline_service
from ui.services.persistence import config_persistence
from ui.services.serializers import serialize_for_store, to_plain_dict
from ui.components.plots import build_signal_corr_plot, build_authenticator_plot, build_almanac_skyplot

# Form builder from ui.components.forms
from ui.components.forms import build_form_from_schema, build_result_display, set_config_init_params, validate_form

# Import from SASpipeline (single external dependency)
from SASpipeline import MODULE_REGISTRY, get_pipeline_state


def register_ntp_callbacks(app: Dash) -> None:
    """Register NTP selector modal callbacks."""
    
    @app.callback(
        Output("ntp-selector-modal", "is_open"),
        Input("open-ntp-selector-btn", "n_clicks"),
        Input("close-ntp-selector", "n_clicks"),
        Input({"type": "ntp-server-card", "address": ALL}, "n_clicks"),
        State("ntp-selector-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_ntp_modal(open_clicks, close_clicks, card_clicks, is_open):
        """Open/close NTP selector modal."""
        ctx = callback_context
        if not ctx.triggered:
            return no_update
        
        trigger_id = ctx.triggered[0]["prop_id"]
        trigger_value = ctx.triggered[0]["value"]
        
        # Only respond if there was an actual click (value > 0)
        if trigger_value is None or trigger_value == 0:
            return no_update
        
        if "open-ntp-selector-btn" in trigger_id:
            return True
        elif "close-ntp-selector" in trigger_id:
            return False
        elif "ntp-server-card" in trigger_id:
            # Card was clicked, close modal
            return False
        
        return no_update
    
    @app.callback(
        Output({"type": "form-field", "block": "TimeReferenceSynchronizer", "field": "selected_ntp_server"}, "value"),
        Output("ntp-selector-label", "children"),
        Input({"type": "ntp-server-card", "address": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def select_ntp_server(card_clicks):
        """Handle NTP server card selection."""
        ctx = callback_context
        if not ctx.triggered:
            return no_update, no_update
        
        # Find which card was clicked
        trigger = ctx.triggered[0]
        trigger_value = trigger["value"]
        trigger_id = trigger["prop_id"]
        
        # Only respond if there was an actual click (value > 0)
        if trigger_value is None or trigger_value == 0:
            return no_update, no_update
        
        # Extract address from the pattern-matching ID
        try:
            import json as json_module
            # Pattern: {"type":"ntp-server-card","address":"time.google.com"}.n_clicks
            id_part = trigger_id.rsplit(".", 1)[0]
            id_dict = json_module.loads(id_part)
            address = id_dict.get("address")
            
            if address:
                # Map address to label
                ntp_labels = {
                    "time.google.com": "Google (time.google.com)",
                    "time.cloudflare.com": "Cloudflare (time.cloudflare.com)",
                    "time.windows.com": "Microsoft (time.windows.com)",
                    "time.apple.com": "Apple (time.apple.com)",
                    "time.nist.gov": "NIST (time.nist.gov)",
                }
                label = ntp_labels.get(address, address)
                return address, label
        except Exception:
            pass
        
        return no_update, no_update


def register_schedule_picker_callbacks(app: Dash) -> None:
    """Register schedule picker modal callbacks.
    
    Note: All times are handled in UTC for GNSS/Galileo system time compatibility.
    """
    from datetime import datetime, timedelta, timezone
    
    @app.callback(
        Output("schedule-picker-modal", "is_open"),
        Input("open-schedule-picker-btn", "n_clicks"),
        Input("close-schedule-picker", "n_clicks"),
        Input("apply-schedule-btn", "n_clicks"),
        Input("schedule-now-btn", "n_clicks"),
        State("schedule-picker-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_schedule_modal(open_clicks, close_clicks, apply_clicks, now_clicks, is_open):
        """Open/close schedule picker modal."""
        ctx = callback_context
        if not ctx.triggered:
            return no_update
        
        trigger_id = ctx.triggered[0]["prop_id"]
        trigger_value = ctx.triggered[0]["value"]
        
        if trigger_value is None or trigger_value == 0:
            return no_update
        
        if "open-schedule-picker-btn" in trigger_id:
            return True
        elif "close-schedule-picker" in trigger_id:
            return False
        elif "apply-schedule-btn" in trigger_id:
            return False
        elif "schedule-now-btn" in trigger_id:
            return False
        
        return no_update
    
    @app.callback(
        Output("schedule-doy-preview", "children"),
        Input("schedule-date-picker", "value"),
        Input("schedule-time-picker", "value"),
        prevent_initial_call=True,
    )
    def update_doy_preview(date_value, time_value):
        """Update the DOY format preview when date/time changes."""
        if not date_value:
            return "Select a date..."
        
        try:
            # Parse the date
            date_obj = datetime.fromisoformat(date_value)
            doy = date_obj.timetuple().tm_yday
            
            # Parse time (format: HH:MM or HH:MM:SS)
            if time_value:
                time_str = time_value
                if isinstance(time_value, str) and "T" in time_value:
                    # Handle ISO format from TimeInput
                    time_str = time_value.split("T")[1][:8]
            else:
                time_str = "00:00:00"
            
            # Ensure HH:MM:SS format
            time_parts = time_str.split(":")
            if len(time_parts) == 2:
                time_str = f"{time_parts[0]}:{time_parts[1]}:00"
            
            doy_format = f"{doy:03d} {time_str}"
            return f"{doy_format} (UTC)"
        except Exception as e:
            return f"Invalid date/time: {e}"
    
    @app.callback(
        Output({"type": "form-field", "block": "ConfigInit", "field": "scheduled_time_doy"}, "value"),
        Output("schedule-picker-label", "children"),
        Input("apply-schedule-btn", "n_clicks"),
        Input("schedule-now-btn", "n_clicks"),
        State("schedule-date-picker", "value"),
        State("schedule-time-picker", "value"),
        prevent_initial_call=True,
    )
    def apply_schedule_selection(apply_clicks, now_clicks, date_value, time_value):
        """Apply the selected schedule to the form."""
        ctx = callback_context
        if not ctx.triggered:
            return no_update, no_update
        
        trigger_id = ctx.triggered[0]["prop_id"]
        trigger_value = ctx.triggered[0]["value"]
        
        if trigger_value is None or trigger_value == 0:
            return no_update, no_update
        
        if "schedule-now-btn" in trigger_id:
            # "Now" button clicked - set value to "0"
            return "0", "Now (immediate recording)"
        
        if "apply-schedule-btn" in trigger_id:
            if not date_value:
                return no_update, no_update
            
            try:
                # Parse the date
                date_obj = datetime.fromisoformat(date_value)
                doy = date_obj.timetuple().tm_yday
                
                # Parse time
                if time_value:
                    time_str = time_value
                    if isinstance(time_value, str) and "T" in time_value:
                        time_str = time_value.split("T")[1][:8]
                else:
                    time_str = "00:00:00"
                
                # Ensure HH:MM:SS format
                time_parts = time_str.split(":")
                if len(time_parts) == 2:
                    time_str = f"{time_parts[0]}:{time_parts[1]}:00"
                
                doy_format = f"{doy:03d} {time_str}"
                
                # Create display label with date (indicate UTC)
                display_label = f"{date_obj.strftime('%b %d')} at {time_str} UTC (DOY {doy:03d})"
                
                return doy_format, display_label
            except Exception:
                return no_update, no_update
        
        return no_update, no_update


def register_callbacks(app: Dash) -> None:
    """Register modal-related callbacks."""
    
    # Also register NTP selector callbacks
    register_ntp_callbacks(app)
    
    # Also register schedule picker callbacks
    register_schedule_picker_callbacks(app)
    
    @app.callback(
        Output("details-modal", "is_open", allow_duplicate=True),
        Input("close-details-modal", "n_clicks"),
        prevent_initial_call=True,
    )
    def close_details_modal(n_clicks):
        """Close the details modal."""
        if n_clicks:
            return False
        return no_update
    
    @app.callback(
        Output("details-modal", "is_open", allow_duplicate=True),
        Output("modal-title", "children", allow_duplicate=True),
        Output("modal-html", "children", allow_duplicate=True),
        Output("modal-plot-html", "children", allow_duplicate=True),
        Output("modal-corr-plot", "figure", allow_duplicate=True),
        Output("modal-graph-container", "style", allow_duplicate=True),
        Output("modal-actions", "children", allow_duplicate=True),
        Output("selected-block-id", "data", allow_duplicate=True),
        Output("debug-click", "children", allow_duplicate=True),
        Input("click-with-timestamp", "data"),
        State("details-modal", "is_open"),
        State("blocks-store", "data"),
        State("recs-parser-store", "data"),
        State("recs-decrypt-store", "data"),
        State("signal-corr-store", "data"),
        State("snapshot-analysis-store", "data"),
        State("authenticator-store", "data"),
        State("time-sync-store", "data"),
        State("almanac-store", "data"),
        State("ephemerides-store", "data"),
        State("pvt-store", "data"),
        prevent_initial_call=True,
    )
    def open_block_modal(
        click_data, modal_is_open, blocks_data,
        recs_parser_data, recs_decrypt_data, signal_corr_data,
        snapshot_data, authenticator_data, time_sync_data, almanac_data, ephemerides_data, pvt_data
    ):
        """Open modal when a block is clicked."""
        if modal_is_open:
            return (no_update,) * 9
        
        if not click_data or not blocks_data:
            return (no_update,) * 9
        
        # Extract node_id from click data
        node_id = click_data.get("node_id")
        if not node_id:
            node_data = click_data.get("node")
            if node_data:
                node_data = node_data[0] if isinstance(node_data, list) else node_data
                node_id = node_data.get("id")
        
        if not node_id:
            return (no_update,) * 9
        
        # Find the block
        block = next((blk for blk in blocks_data if blk["id"] == node_id), None)
        if not block:
            return (no_update,) * 9
        
        bid = block["id"]
        params_dict = block.get("params", {}) if isinstance(block.get("params"), dict) else {}
        
        # Get block info from registry
        block_info = MODULE_REGISTRY.get(bid)
        
        # Build form from schema + results
        html_info = None
        result_section = None
        fig = go.Figure()
        
        # Extract ConfigInit params for status updates
        config_init_block = next((blk for blk in blocks_data if blk.get("id") == "ConfigInit"), None)
        config_init_params = config_init_block.get("params", {}) if config_init_block else {}
        
        if block_info:
            load_schema = block_info.get("load_schema")
            if callable(load_schema):
                try:
                    schema = load_schema()
                    set_config_init_params(config_init_params)
                    
                    # Build description header if available
                    block_description = schema.get("description", "")
                    description_html = html.P(
                        block_description, 
                        className="text-muted mb-3",
                        style={"fontStyle": "italic"}
                    ) if block_description else html.Div()
                    
                    form_html = build_form_from_schema(schema, params_dict, block_id=bid)
                    
                    # Get result data based on block type
                    result_data = _get_result_data_for_block(
                        bid, recs_parser_data, recs_decrypt_data,
                        signal_corr_data, snapshot_data, time_sync_data, ephemerides_data, almanac_data
                    )
                    
                    if result_data and (schema.get("result_fields") or schema.get("result_display")):
                        inputs_col, outputs_col = build_result_display(schema, result_data)
                        result_section = html.Div([
                            html.Hr(className="my-4"),
                            html.H5("Results", className="fw-bold mb-3"),
                            dbc.Row([inputs_col, outputs_col]),
                        ])
                    
                    html_info = html.Div([
                        description_html,
                        form_html,
                        result_section if result_section else html.Div(),
                    ])
                    
                except Exception as e:
                    print(f"Error building form for {bid}: {e}")
                    traceback.print_exc()
                    html_info = dbc.Alert(f"Error building form: {e}", color="danger")
            else:
                html_info = dbc.Alert("No schema available for this block.", color="warning")
        else:
            html_info = dbc.Alert(f"Block '{bid}' not found in registry.", color="warning")
        
        graph_style = {"display": "none"}
        
        # Build plot HTML based on block type
        # Use PipelineState as fallback if UI store is empty
        plot_html = html.Div()
        if bid == "SignalCorrelation":
            plot_data = signal_corr_data
            if not plot_data:
                try:
                    plot_data = get_pipeline_state().get_latest_result("SignalCorrelation")
                except Exception:
                    pass
            plot_html = build_signal_corr_plot(plot_data)
        elif bid == "SatelliteAuthenticator":
            plot_data = authenticator_data
            if not plot_data:
                try:
                    plot_data = get_pipeline_state().get_latest_result("SatelliteAuthenticator")
                except Exception:
                    pass
            plot_html = build_authenticator_plot(plot_data)
        elif bid == "AlmanacManagement":
            plot_data = almanac_data
            if not plot_data:
                try:
                    plot_data = get_pipeline_state().get_latest_result("AlmanacManagement")
                except Exception:
                    pass
            plot_html = build_almanac_skyplot(plot_data)
        
        actions = [
            dbc.Button("Save", id="save-block-params", color="success", className="me-2"),
            dbc.Button("Run", id="run-block-btn", color="primary", className="me-2"),
            dbc.Button("Edit JSON", id="open-json", color="secondary", className="me-2"),
        ]
        
        return (
            True,
            block["name"],
            html_info,
            plot_html,
            fig,
            graph_style,
            html.Div(actions),
            bid,
            f"Modal opened for block {bid}",
        )
    
    @app.callback(
        Output("modal-save-feedback", "children"),
        Output("blocks-store", "data", allow_duplicate=True),
        Input("save-block-params", "n_clicks"),
        State({"type": "form-field", "block": ALL, "field": ALL}, "value"),
        State({"type": "form-field", "block": ALL, "field": ALL}, "id"),
        State("selected-block-id", "data"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def save_block_params_from_form(n_clicks, field_values, field_ids, block_id, blocks_data):
        """Save form field values to config_json file and update blocks-store."""
        if not n_clicks or not block_id or not blocks_data:
            return no_update, no_update
        
        # Build params dict from form fields
        form_params = {}
        for field_id, value in zip(field_ids, field_values):
            if field_id.get("block") == block_id:
                field_name = field_id.get("field")
                form_params[field_name] = value
        
        if not form_params:
            return dbc.Alert("No fields to save", color="warning", duration=3000), no_update
        
        # Validate form fields against schema
        block_info = MODULE_REGISTRY.get(block_id)
        if block_info:
            load_schema = block_info.get("load_schema")
            if callable(load_schema):
                try:
                    schema = load_schema()
                    validation_errors = validate_form(schema, form_params)
                    if validation_errors:
                        error_list = html.Ul([html.Li(err) for err in validation_errors])
                        return (
                            dbc.Alert(
                                [html.Strong("Validation errors:"), error_list],
                                color="danger",
                                duration=8000,
                            ),
                            no_update,
                        )
                except Exception as e:
                    print(f"Warning: Could not validate schema for {block_id}: {e}")
        
        try:
            # Load existing config to preserve fields not in the form
            existing_params = config_persistence.load(block_id) or {}
            
            # Merge: existing params + form params (form values take precedence)
            new_params = {**existing_params, **form_params}
            
            # Persist to file
            file_path = config_persistence.save(block_id, new_params)
            
            # Update status to CONFIGURED since user explicitly saved config
            updated_blocks = _update_block_params(
                blocks_data, 
                block_id, 
                new_params, 
                new_status="CONFIGURED"
            )
            
            log_manager.append(f"Block {block_id} params saved and updated")
            
            return (
                dbc.Alert(f"Saved to {config_persistence.format_path(file_path)}", color="success", duration=3000),
                updated_blocks,
            )
        except Exception as e:
            return dbc.Alert(f"Error: {e}", color="danger", duration=5000), no_update
    
    @app.callback(
        Output("modal-save-feedback", "children", allow_duplicate=True),
        Output("blocks-store", "data", allow_duplicate=True),
        Output("recs-parser-store", "data", allow_duplicate=True),
        Output("recs-decrypt-store", "data", allow_duplicate=True),
        Output("signal-corr-store", "data", allow_duplicate=True),
        Output("snapshot-analysis-store", "data", allow_duplicate=True),
        Output("authenticator-store", "data", allow_duplicate=True),
        Output("time-sync-store", "data", allow_duplicate=True),
        Output("almanac-store", "data", allow_duplicate=True),
        Output("ephemerides-store", "data", allow_duplicate=True),
        Output("pvt-store", "data", allow_duplicate=True),
        Output("modal-html", "children", allow_duplicate=True),
        Input("run-block-btn", "n_clicks"),
        State("selected-block-id", "data"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def run_single_module(n_clicks, block_id, blocks_data):
        """Run a single module when the Run button is clicked."""
        if not n_clicks or not block_id or not blocks_data:
            return (no_update,) * 12

        # Get current params for the module
        block = next((blk for blk in blocks_data if blk.get("id") == block_id), None)
        if not block:
            return (
                dbc.Alert(f"Module {block_id} not found", color="danger", duration=3000),
                no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update
            )

        params = block.get("params", {})

        log_manager.append(f"Running module {block_id}...")

        try:
            # Run the module
            result = pipeline_service.run_module(block_id, params)
            
            if result["success"]:
                # Update block status to SUCCESS
                updated_blocks = _update_block_params(
                    blocks_data, block_id, params, new_status="SUCCESS"
                )
                
                # Determine which store to update based on block_id
                store_updates = {
                    "recs-parser-store": no_update,
                    "recs-decrypt-store": no_update,
                    "signal-corr-store": no_update,
                    "snapshot-analysis-store": no_update,
                    "authenticator-store": no_update,
                    "time-sync-store": no_update,
                    "almanac-store": no_update,
                    "ephemerides-store": no_update,
                    "pvt-store": no_update,
                }
                
                result_data = result.get("data")
                if result_data:
                    if block_id == "BGDandRECSparser":
                        store_updates["recs-parser-store"] = serialize_for_store(result_data)
                    elif block_id == "RECSDecryption":
                        store_updates["recs-decrypt-store"] = serialize_for_store(result_data)
                    elif block_id == "SignalCorrelation":
                        store_updates["signal-corr-store"] = serialize_for_store(result_data)
                    elif block_id == "SnapshotRecording":
                        store_updates["snapshot-analysis-store"] = serialize_for_store(result_data)
                    elif block_id == "SatelliteAuthenticator":
                        store_updates["authenticator-store"] = serialize_for_store(result_data)
                    elif block_id == "TimeReferenceSynchronizer":
                        store_updates["time-sync-store"] = serialize_for_store(result_data)
                    elif block_id == "AlmanacManagement":
                        store_updates["almanac-store"] = serialize_for_store(result_data)
                    elif block_id == "EphemeridesManagement":
                        store_updates["ephemerides-store"] = serialize_for_store(result_data)
                    elif block_id == "PVTComputation":
                        store_updates["pvt-store"] = serialize_for_store(result_data)
                
                # Rebuild modal content with results
                modal_content = no_update
                block_info = MODULE_REGISTRY.get(block_id)
                if block_info and result_data:
                    load_schema = block_info.get("load_schema")
                    if callable(load_schema):
                        try:
                            schema = load_schema()
                            block_description = schema.get("description", "")
                            description_html = html.P(
                                block_description, 
                                className="text-muted mb-3",
                                style={"fontStyle": "italic"}
                            ) if block_description else html.Div()
                            
                            form_html = build_form_from_schema(schema, params, block_id=block_id)
                            
                            # Build result section
                            result_section = html.Div()
                            if schema.get("result_fields") or schema.get("result_display"):
                                inputs_col, outputs_col = build_result_display(schema, result_data)
                                result_section = html.Div([
                                    html.Hr(className="my-4"),
                                    html.H5("Results", className="fw-bold mb-3"),
                                    dbc.Row([inputs_col, outputs_col]),
                                ])
                            
                            modal_content = html.Div([
                                description_html,
                                form_html,
                                result_section,
                            ])
                        except Exception as e:
                            print(f"Error rebuilding modal for {block_id}: {e}")
                
                return (
                    dbc.Alert(f"{block_id} executed successfully", color="success", duration=3000),
                    updated_blocks,
                    store_updates["recs-parser-store"],
                    store_updates["recs-decrypt-store"],
                    store_updates["signal-corr-store"],
                    store_updates["snapshot-analysis-store"],
                    store_updates["authenticator-store"],
                    store_updates["time-sync-store"],
                    store_updates["almanac-store"],
                    store_updates["ephemerides-store"],
                    store_updates["pvt-store"],
                    modal_content,
                )
            else:
                # Update block status to ERROR
                updated_blocks = _update_block_params(
                    blocks_data, block_id, params, new_status="ERROR"
                )
                
                return (
                    dbc.Alert(f"Error: {result.get('error', 'Unknown error')}", color="danger", duration=5000),
                    updated_blocks,
                    no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update
                )
                
        except Exception as e:
            log_manager.append(f"Error running {block_id}: {e}")
            return (
                dbc.Alert(f"Error: {e}", color="danger", duration=5000),
                no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update
            )
    
    @app.callback(
        Output("modal-html", "children", allow_duplicate=True),
        Input({"type": "form-field", "block": ALL, "field": ALL}, "value"),
        State({"type": "form-field", "block": ALL, "field": ALL}, "id"),
        State("selected-block-id", "data"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def update_form_on_field_change(field_values, field_ids, block_id, blocks_data):
        """
        Rebuild the form when a field that has dependents changes.
        This ensures dependent fields update their disabled state in real-time.
        """
        if not block_id or not field_ids:
            return no_update
        
        # Fields that control the disabled state of other fields
        trigger_fields = {"bgd_file_enabled", "tesla_file_enabled", "rf_input_from_file", 
                         "save_config_on_run", "auxiliary_mode", "calibration_active"}
        
        # Get what triggered this callback
        ctx = callback_context
        if not ctx.triggered:
            return no_update
        
        triggered_id = ctx.triggered[0]["prop_id"]
        
        # Check if the triggered field is one that controls dependencies
        is_dependency_trigger = False
        for trigger in trigger_fields:
            if trigger in triggered_id:
                is_dependency_trigger = True
                break
        
        if not is_dependency_trigger:
            return no_update
        
        # Build current form_values from the callback inputs
        form_values = {}
        for field_id, value in zip(field_ids, field_values):
            if field_id.get("block") == block_id:
                form_values[field_id.get("field")] = value
        
        # Get the schema for this block using load_schema function
        block_info = MODULE_REGISTRY.get(block_id)
        if not block_info:
            return no_update
        
        load_schema = block_info.get("load_schema")
        if not callable(load_schema):
            return no_update
        
        try:
            schema = load_schema()
            
            # Set ConfigInit params for cross-block dependencies
            config_init_block = next((blk for blk in (blocks_data or []) if blk.get("id") == "ConfigInit"), None)
            if config_init_block:
                set_config_init_params(config_init_block.get("params", {}))
            
            # Rebuild the form with updated dependencies
            form_html = build_form_from_schema(schema, form_values, block_id=block_id)
            
            log_manager.append(f"Form updated for {block_id} (dependency changed)")
            
            return form_html
        except Exception as e:
            log_manager.append(f"Error updating form: {e}")
            return no_update
    
    # JSON Editor callbacks
    @app.callback(
        Output("json-modal", "is_open"),
        Output("json-editor-dmc", "value"),
        Output("json-modal-title", "children"),
        Output("json-file-select", "data"),
        Output("json-file-select", "value"),
        Input("open-json", "n_clicks"),
        Input("close-json", "n_clicks"),
        State("selected-block-id", "data"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def handle_json_editor_modal(n_open, n_close, block_node_id, blocks_data):
        """Handle JSON editor modal open/close."""
        ctx = callback_context
        if not ctx.triggered:
            return False, no_update, no_update, no_update, no_update
        
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        
        if trigger_id == "close-json":
            return False, no_update, no_update, no_update, no_update
        
        if trigger_id == "open-json" and block_node_id:
            if not n_open:
                return False, no_update, no_update, no_update, no_update
            
            block = next((b for b in (blocks_data or []) if b.get("id") == block_node_id), None)
            if not block:
                return False, no_update, no_update, no_update, no_update
            
            json_string = json.dumps(block.get("params", {}), indent=2, ensure_ascii=False)
            modal_title = f"JSON editor — {block.get('name', block_node_id)}"
            
            options = config_persistence.list_files(block_node_id)
            preferred_value = f"{block_node_id}.json"
            selected_value = None
            if options:
                candidates = [opt["value"] for opt in options]
                selected_value = preferred_value if preferred_value in candidates else candidates[0]
            
            return True, json_string, modal_title, options, selected_value
        
        return False, no_update, no_update, no_update, no_update
    
    @app.callback(
        Output("blocks-store", "data", allow_duplicate=True),
        Output("json-save-alert", "children", allow_duplicate=True),
        Output("json-save-alert", "color", allow_duplicate=True),
        Output("json-save-alert", "hide", allow_duplicate=True),
        Output("json-save-alert", "title", allow_duplicate=True),
        Output("json-editor-dmc", "value", allow_duplicate=True),
        Input("save-json", "n_clicks"),
        State("json-editor-dmc", "value"),
        State("selected-block-id", "data"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def save_json_changes(n_clicks, editor_content, block_node_id, blocks_data):
        """Save JSON editor content and update blocks-store."""
        if not n_clicks or not editor_content or not block_node_id:
            return no_update, no_update, no_update, True, no_update, no_update
        
        try:
            new_params = json.loads(editor_content)
            if not isinstance(new_params, dict):
                raise TypeError("JSON payload must be an object.")
            
            # Update blocks-store with new params and status
            updated_blocks = _update_block_params(
                blocks_data, 
                block_node_id, 
                new_params,
                new_status="CONFIGURED"
            )
            
            # Persist to disk
            file_path = config_persistence.save(block_node_id, new_params)
            
            log_manager.append(f"Block {block_node_id} JSON saved and updated")
            
            formatted = json.dumps(new_params, indent=2, ensure_ascii=False)
            message = f"Parameters saved to {config_persistence.format_path(file_path)}."
            return (updated_blocks, message, "green", False, "Success", formatted)
            
        except (json.JSONDecodeError, TypeError) as e:
            return (no_update, f"Error: {e}", "red", False, "Format error", no_update)


def _get_result_data_for_block(block_id, recs_parser_data, recs_decrypt_data, signal_corr_data, snapshot_data, time_sync_data=None, ephemerides_data=None, almanac_data=None):
    """
    Get the appropriate result data for a block.
    
    First checks the UI store data passed as arguments.
    If store is empty, falls back to PipelineState results.
    """
    # Mapping of block IDs to their UI store data
    store_mapping = {
        "BGDandRECSparser": recs_parser_data,
        "RECSDecryption": recs_decrypt_data,
        "SignalCorrelation": signal_corr_data,
        "SnapshotRecording": snapshot_data,
        "TimeReferenceSynchronizer": time_sync_data,
        "AlmanacManagement": almanac_data,
        "EphemeridesManagement": ephemerides_data,
    }
    
    # First, try the UI store
    result = store_mapping.get(block_id)
    
    # If UI store is empty, try PipelineState
    if not result:
        try:
            pipeline_state = get_pipeline_state()
            result = pipeline_state.get_latest_result(block_id)
        except Exception:
            pass
    
    return result


def _update_block_params(blocks_data, block_id, new_params, new_status=None):
    """
    Update params (and optionally status) for a specific block in blocks_data.
    
    Args:
        blocks_data: List of block dicts
        block_id: ID of the block to update
        new_params: New params dict
        new_status: Optional new status string (e.g., "READY", "CONFIGURED")
    
    Returns:
        Updated list of block dicts
    """
    updated_blocks = []
    for blk in blocks_data or []:
        if blk.get("id") == block_id:
            updated_blk = {**blk, "params": new_params}
            if new_status:
                updated_blk["status"] = new_status
            updated_blocks.append(updated_blk)
        else:
            updated_blocks.append(blk)
    return updated_blocks
