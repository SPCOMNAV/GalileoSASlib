"""
Form Components
---------------
Dynamic form building from JSON schemas for the new UI.

This module provides functions to dynamically generate Dash forms
based on JSON schema definitions from default_json/<block>_schema.json.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dash import dcc, html, no_update
from dash.dependencies import Input, Output, State
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# ConfigInit helpers
# ---------------------------------------------------------------------------

_current_config_init_params = {}


def set_config_init_params(params: dict):
    """Set the current ConfigInit params for status fields."""
    global _current_config_init_params
    _current_config_init_params = params or {}


def _get_config_init_value(key: str, default=None):
    """Read a value from ConfigInit from the current store or file."""
    global _current_config_init_params
    if _current_config_init_params and key in _current_config_init_params:
        return _current_config_init_params.get(key, default)
    try:
        from SASpipeline import get_module_config
        cfg = get_module_config("ConfigInit")
        return cfg.get(key, default)
    except Exception:
        return default


def _build_config_status_field(
    label: str, 
    config_key: str, 
    true_text: str = "ACTIVE", 
    false_text: str = "INACTIVE",
    true_color: str = "#2e7d32",
    true_bg: str = "#e8f5e9",
    false_color: str = "#c62828",
    false_bg: str = "#ffebee",
):
    """Build a readonly TextInput showing a ConfigInit variable status."""
    value = _get_config_init_value(config_key, False)
    
    if config_key == "svid_encrypted":
        is_active = bool(value and str(value).strip())
    else:
        is_active = bool(value)
    
    return dmc.TextInput(
        label=label,
        value=true_text if is_active else false_text,
        disabled=True,
        style={"marginBottom": "16px"},
        styles={
            "input": {
                "fontWeight": "bold",
                "color": true_color if is_active else false_color,
                "backgroundColor": true_bg if is_active else false_bg,
            }
        },
    )


# ---------------------------------------------------------------------------
# Common styles
# ---------------------------------------------------------------------------

VALUE_STYLE = {
    "color": "#d6336c",
    "font-size": "1em",
    "word-break": "break-all",
}

LABEL_STYLE = {
    "font-weight": "bold",
    "margin-right": "8px",
}


# ---------------------------------------------------------------------------
# Field Validation
# ---------------------------------------------------------------------------

# Valid Galileo SVIDs (Space Vehicle IDs)
VALID_GALILEO_SVIDS = set(range(1, 37))  # 1-36


def _validate_int_list(value: str, min_val: int = None, max_val: int = None, 
                       valid_set: set = None, label: str = "Field") -> Optional[str]:
    """
    Validate a comma-separated list of integers.
    
    Args:
        value: Comma-separated string of integers
        min_val: Minimum allowed value for each integer
        max_val: Maximum allowed value for each integer
        valid_set: Optional set of valid values
        label: Field label for error messages
    
    Returns:
        Error message if invalid, None if valid
    """
    if not value or not str(value).strip():
        return None
    
    invalid_items = []
    out_of_range = []
    
    for item in str(value).split(","):
        item = item.strip()
        if not item:
            continue
        
        try:
            int_val = int(item)
            
            # Check against valid set (e.g., valid SVIDs)
            if valid_set is not None and int_val not in valid_set:
                invalid_items.append(str(int_val))
            # Or check min/max range
            elif min_val is not None and int_val < min_val:
                out_of_range.append(f"{int_val} < {min_val}")
            elif max_val is not None and int_val > max_val:
                out_of_range.append(f"{int_val} > {max_val}")
                
        except ValueError:
            invalid_items.append(item)
    
    errors = []
    if invalid_items:
        errors.append(f"invalid values: {', '.join(invalid_items)}")
    if out_of_range:
        errors.append(f"out of range: {', '.join(out_of_range)}")
    
    if errors:
        return f"{label}: {'; '.join(errors)}"
    
    return None


def validate_field(field: dict, value: Any) -> Optional[str]:
    """
    Validate a field value against its schema definition.
    
    Args:
        field: Field definition from schema
        value: Current value to validate
    
    Returns:
        Error message string if invalid, None if valid
    """
    field_id = field.get("id", "unknown")
    label = field.get("label", field_id)
    field_type = field.get("type", "string")
    props = field.get("props", {})
    optional = field.get("optional", False)
    validation = field.get("validation")  # Custom validation type
    
    # Skip validation for empty optional fields
    if optional and (value is None or value == "" or value == []):
        return None
    
    # Required field check (non-optional fields)
    if not optional and (value is None or value == ""):
        # Some fields allow empty strings as valid
        if field_type not in ("string",):
            return f"{label}: field is required"
    
    # Custom validation types
    if validation:
        if validation == "svid_list":
            # Validate as comma-separated list of Galileo SVIDs (1-36)
            return _validate_int_list(value, valid_set=VALID_GALILEO_SVIDS, label=label)
        elif validation == "int_list":
            # Validate as comma-separated list of integers with optional range
            min_val = props.get("listMin")
            max_val = props.get("listMax")
            return _validate_int_list(value, min_val=min_val, max_val=max_val, label=label)
    
    # Type-specific validation
    if field_type == "int":
        if value is not None and value != "":
            try:
                int_val = int(float(value))
                
                # Min/max checks
                min_val = props.get("min")
                max_val = props.get("max")
                
                if min_val is not None and int_val < min_val:
                    return f"{label}: value {int_val} is below minimum ({min_val})"
                if max_val is not None and int_val > max_val:
                    return f"{label}: value {int_val} exceeds maximum ({max_val})"
                    
            except (ValueError, TypeError):
                return f"{label}: must be a valid integer"
    
    elif field_type == "float":
        if value is not None and value != "":
            try:
                float_val = float(value)
                
                # Min/max checks
                min_val = props.get("min")
                max_val = props.get("max")
                
                if min_val is not None and float_val < min_val:
                    return f"{label}: value {float_val} is below minimum ({min_val})"
                if max_val is not None and float_val > max_val:
                    return f"{label}: value {float_val} exceeds maximum ({max_val})"
                    
            except (ValueError, TypeError):
                return f"{label}: must be a valid number"
    
    elif field_type == "bool":
        # Booleans are usually switches, accept bool or truthy/falsy
        pass
    
    elif field_type == "string":
        # Pattern validation (e.g., for hex strings)
        pattern = props.get("pattern")
        if pattern and value:
            if not re.match(pattern, str(value)):
                return f"{label}: invalid format"
        
        # Min/max length
        min_len = props.get("minLength")
        max_len = props.get("maxLength")
        
        if value and min_len is not None and len(str(value)) < min_len:
            return f"{label}: too short (minimum {min_len} characters)"
        if value and max_len is not None and len(str(value)) > max_len:
            return f"{label}: too long (maximum {max_len} characters)"
    
    return None


def validate_form(schema: dict, form_params: dict) -> List[str]:
    """
    Validate all form fields against their schema definitions.
    
    Args:
        schema: Block schema with sections and fields
        form_params: Dict of field_id -> value from form
    
    Returns:
        List of error messages (empty if all valid)
    """
    errors = []
    
    sections = schema.get("sections", [])
    for section in sections:
        fields = section.get("fields", [])
        for field in fields:
            field_id = field.get("id")
            if field_id is None:
                continue
            
            value = form_params.get(field_id)
            error = validate_field(field, value)
            if error:
                errors.append(error)
    
    return errors


# ---------------------------------------------------------------------------
# Form component building
# ---------------------------------------------------------------------------

def build_form_component(field, value, block_id: str = None, form_values: dict = None):
    """
    Build a Dash component for a form field.
    
    Args:
        field: Dict with field definition
        value: Current field value
        block_id: Block ID for pattern matching
        form_values: All form values (for dependsOn evaluation)
    
    Returns:
        Dash component
    """
    field_id = field.get("id")
    component_type = field.get("component", "input")
    props = dict(field.get("props", {}))
    placeholder = field.get("placeholder", "")
    
    if block_id:
        component_id = {"type": "form-field", "block": block_id, "field": field_id}
    else:
        component_id = field_id
    
    # Check config_source to read value from another config
    config_source = field.get("config_source")
    if config_source:
        try:
            from SASpipeline import load_json_file, CONFIG_DIR
            source_config = load_json_file(CONFIG_DIR / f"{config_source}.json")
            json_path = field.get("path", field_id)
            value = source_config.get(json_path, field.get("default"))
        except Exception:
            value = field.get("default")
    
    if value is None:
        value = field.get("default")
    
    display_value = "" if value is None else value
    
    # Check dependsOn
    is_disabled = False
    depends_on = field.get("dependsOn")
    if depends_on:
        depends_field = depends_on.get("field")
        depends_value = depends_on.get("value")
        if depends_field:
            # If config_source is set, read dependsOn value from source config
            if config_source:
                try:
                    from SASpipeline import load_json_file, CONFIG_DIR
                    source_config = load_json_file(CONFIG_DIR / f"{config_source}.json")
                    current_depends_value = source_config.get(depends_field)
                except Exception:
                    current_depends_value = None
            elif form_values:
                current_depends_value = form_values.get(depends_field)
            else:
                current_depends_value = None
            
            if current_depends_value is not None:
                if isinstance(depends_value, list):
                    if current_depends_value not in depends_value:
                        is_disabled = True
                else:
                    if current_depends_value != depends_value:
                        is_disabled = True
            else:
                # If we can't get the value, disable it
                is_disabled = True
    
    # Check dependsOnBlock
    depends_on_block = field.get("dependsOnBlock")
    if depends_on_block:
        other_block = depends_on_block.get("block")
        other_field = depends_on_block.get("field")
        other_value = depends_on_block.get("value")
        disable_when = depends_on_block.get("disableWhen", "equals")
        
        try:
            from SASpipeline import load_json_file, CONFIG_DIR
            other_config = load_json_file(CONFIG_DIR / f"{other_block}.json")
            current_other_value = other_config.get(other_field)
            
            if disable_when == "equals":
                if current_other_value == other_value:
                    is_disabled = True
            elif disable_when == "notEquals":
                if current_other_value != other_value:
                    is_disabled = True
        except Exception:
            pass
    
    # Check disabled_when (simple conditional disable within same form)
    disabled_when = field.get("disabled_when")
    if disabled_when and form_values:
        disable_field = disabled_when.get("field")
        disable_value = disabled_when.get("value")
        if disable_field:
            current_disable_value = form_values.get(disable_field)
            if current_disable_value == disable_value:
                is_disabled = True
    
    # Readonly component
    if component_type == "readonly":
        return dbc.Input(
            id=component_id,
            value=str(display_value),
            disabled=True,
            style={
                "backgroundColor": "#f8f9fa",
                "fontStyle": "italic",
                "color": "#6c757d"
            }
        )
    
    if component_type == "input":
        input_props = dict(props)
        input_props.setdefault("type", "text")
        if placeholder:
            input_props.setdefault("placeholder", placeholder)
        return dbc.Input(
            id=component_id,
            value=display_value,
            disabled=is_disabled,
            style={"opacity": "0.5"} if is_disabled else {},
            **input_props
        )
    
    elif component_type == "textarea":
        textarea_props = dict(props)
        if placeholder:
            textarea_props.setdefault("placeholder", placeholder)
        return dbc.Textarea(
            id=component_id,
            value=display_value,
            disabled=is_disabled,
            style={"opacity": "0.5"} if is_disabled else {},
            **textarea_props
        )
    
    elif component_type in ("switch", "checkbox"):
        return dbc.Switch(id=component_id, value=bool(value), **props)
    
    elif component_type in ("dropdown", "select"):
        options = field.get("options") or props.pop("options", [])
        if options and not isinstance(options[0], dict):
            options = [{"label": str(opt), "value": opt} for opt in options]
        return dcc.Dropdown(
            id=component_id,
            value=value if value not in ("", None) else None,
            options=options,
            placeholder=placeholder,
            clearable=False,
            disabled=is_disabled,
            style={"opacity": "0.5"} if is_disabled else {},
            **props,
        )
    
    elif component_type == "ntp_server_selector":
        # Special component: Button that opens NTP server selection popup
        options = field.get("options") or []
        current_label = value or "Select NTP Server..."
        # Find label for current value
        for opt in options:
            if opt.get("value") == value:
                current_label = opt.get("label", value)
                break
        
        return html.Div([
            # Hidden input to store the selected value
            dcc.Input(
                id=component_id,
                type="hidden",
                value=value,
            ),
            # Button to open the NTP selector popup
            dbc.Button(
                [
                    html.I(className="fas fa-clock me-2"),
                    html.Span(current_label, id="ntp-selector-label"),
                ],
                id="open-ntp-selector-btn",
                color="primary",
                outline=True,
                className="w-100",
                style={"textAlign": "left"},
            ),
        ])
    
    elif component_type == "schedule_picker":
        # Special component: Button that opens datetime picker popup
        # Value is in DOY HH:MM:SS format (e.g., "039 14:30:00")
        # All times are in UTC for GNSS/Galileo compatibility
        display_label = f"{value} (UTC)" if value and value != "0" else "Select date and time (UTC)..."
        if value == "0":
            display_label = "Now (immediate recording)"
        
        return html.Div([
            # Hidden input to store the selected value (DOY HH:MM:SS format, interpreted as UTC)
            dcc.Input(
                id=component_id,
                type="hidden",
                value=value,
            ),
            # Button to open the datetime picker popup
            dbc.Button(
                [
                    html.I(className="fas fa-calendar-alt me-2"),
                    html.Span(display_label, id="schedule-picker-label"),
                ],
                id="open-schedule-picker-btn",
                color="primary",
                outline=True,
                className="w-100",
                style={"textAlign": "left"},
                disabled=is_disabled,
            ),
        ])
    
    return dbc.Input(id=field_id, value=display_value, disabled=is_disabled)


def _get_value_by_path(data: dict, path: str, default=None):
    """Get a value from a nested dict using dot notation."""
    if not data or not path:
        return default
    
    keys = path.split(".")
    current = data
    
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    
    return current


def _get_config_status_fields_for_block(block_id: str) -> List:
    """Get ConfigInit status fields based on block type."""
    fields = []
    
    if block_id == "UncertaintyModule":
        fields.append(_build_config_status_field(
            "Module status (from ConfigInit.calibration_active)",
            "calibration_active"
        ))
    elif block_id == "BGDandRECSdownloader":
        fields.append(_build_config_status_field(
            "RECS enabled (from ConfigInit.svid_encrypted)",
            "svid_encrypted"
        ))
        fields.append(_build_config_status_field(
            "BGD enabled (from ConfigInit.bgd_file_enabled)",
            "bgd_file_enabled"
        ))
    elif block_id == "BGDandRECSparser":
        fields.append(_build_config_status_field(
            "RECS parsing enabled (from ConfigInit.svid_encrypted)",
            "svid_encrypted"
        ))
        fields.append(_build_config_status_field(
            "BGD parsing enabled (from ConfigInit.bgd_file_enabled)",
            "bgd_file_enabled"
        ))
    elif block_id == "TESLAkeyManagement":
        fields.append(_build_config_status_field(
            "Key source (from ConfigInit.tesla_file_enabled)",
            "tesla_file_enabled",
            true_text="📁 LOCAL FILE",
            false_text="🌐 ONLINE (download)",
            true_color="#1565c0",
            true_bg="#e3f2fd",
            false_color="#2e7d32",
            false_bg="#e8f5e9",
        ))
        fields.append(_build_config_status_field(
            "TESLA keys needed (from ConfigInit.svid_encrypted)",
            "svid_encrypted"
        ))
    elif block_id == "RECSDecryption":
        fields.append(_build_config_status_field(
            "Decryption needed (from ConfigInit.svid_encrypted)",
            "svid_encrypted"
        ))
    elif block_id == "SatelliteAuthenticator":
        fields.append(_build_config_status_field(
            "Encrypted satellites to authenticate (from ConfigInit.svid_encrypted)",
            "svid_encrypted"
        ))
    elif block_id == "SnapshotRecording":
        fields.append(_build_config_status_field(
            "Input mode (from ConfigInit.rf_input_from_file)",
            "rf_input_from_file",
            true_text="📁 MANUAL (from file)",
            false_text="📡 SDR (live capture)",
            true_color="#1565c0",
            true_bg="#e3f2fd",
            false_color="#e65100",
            false_bg="#fff3e0",
        ))
    
    return fields


def build_form_from_schema(schema, form_values, block_id: str = None):
    """
    Build the complete form from the schema.
    
    Args:
        schema: Dict with block schema
        form_values: Dict with block configuration
        block_id: Block ID for ConfigInit fields
    
    Returns:
        dbc.Form with all fields
    """
    sections = schema.get("sections", [])
    
    if not sections:
        return html.Div(dbc.Alert("No form schema available.", color="warning"))
    
    form_children = []
    
    # Add ConfigInit status fields
    config_status_fields = _get_config_status_fields_for_block(block_id)
    if config_status_fields:
        form_children.append(html.Div(config_status_fields, style={"marginBottom": "16px"}))
    
    for section in sections:
        title = section.get("title", "")
        if title:
            form_children.append(html.H5(title, className="mt-2"))
        
        description = section.get("description", "")
        if description:
            form_children.append(html.P(description, className="text-muted small"))
        
        fields = section.get("fields", [])
        current_row = []
        current_width = 0
        
        for field in fields:
            field_id = field.get("id")
            if not field_id:
                continue
            
            width = field.get("width", 12)
            label = field.get("label", field_id)
            show_label = field.get("show_label", True)
            
            path = field.get("path", field_id)
            value = _get_value_by_path(form_values, path)
            
            if value is None and field_id in form_values:
                value = form_values[field_id]
            
            component = build_form_component(field, value, block_id=block_id, form_values=form_values)
            
            depends_on = field.get("dependsOn")
            is_disabled = False
            if depends_on:
                depends_field = depends_on.get("field")
                depends_value = depends_on.get("value")
                if depends_field:
                    current_depends_value = form_values.get(depends_field)
                    if current_depends_value != depends_value:
                        is_disabled = True
            
            col_children = []
            if show_label:
                label_style = {"opacity": "0.5"} if is_disabled else {}
                col_children.append(dbc.Label(label, html_for=field_id, className="fw-semibold", style=label_style))
            col_children.append(component)
            
            help_text = field.get("help")
            if help_text:
                help_style = {"fontSize": "0.8em", "color": "#6c757d", "marginTop": "2px"}
                if is_disabled:
                    help_style["opacity"] = "0.5"
                col_children.append(html.Small(help_text, style=help_style))
            
            current_row.append(dbc.Col(col_children, width=width))
            current_width += width
            
            if current_width >= 12:
                form_children.append(dbc.Row(current_row, className="g-2 mb-2"))
                current_row = []
                current_width = 0
        
        if current_row:
            form_children.append(dbc.Row(current_row, className="g-2 mb-2"))
        
        form_children.append(html.Hr(className="my-3"))
    
    if form_children and isinstance(form_children[-1], html.Hr):
        form_children.pop()
    
    return dbc.Form(form_children)


# ---------------------------------------------------------------------------
# Result display building
# ---------------------------------------------------------------------------

def get_nested_value(data, key_path):
    """Get a nested value using dot notation."""
    if not data or not key_path:
        return None
    
    keys = key_path.split(".")
    current = data
    
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    
    return current


def build_result_display(schema, result_data):
    """
    Build the result display according to the schema.
    
    Supports both 'result_display' (with inputs/outputs) and 'result_fields' formats.
    
    Args:
        schema: Dict with block schema
        result_data: Dict with pipeline results
    
    Returns:
        Tuple (inputs_column, outputs_column)
    """
    result_display = schema.get("result_display", {})
    result_fields = schema.get("result_fields", [])
    
    if not result_data or not isinstance(result_data, dict):
        return (
            dbc.Col(
                dmc.Alert("Run the pipeline to see results.", color="gray", title="No data yet"),
                width=12,
            ),
            dbc.Col(width=0),
        )
    
    # If using result_fields format (simpler format)
    if result_fields and not result_display:
        result_items = []
        svg_items = []  # SVG images shown separately
        
        for field in result_fields:
            field_id = field.get("id", "")
            label = field.get("label", field_id)
            field_type = field.get("type", "string")
            
            value = result_data.get(field_id)
            
            # Handle SVG type specially
            if field_type == "svg":
                if value and isinstance(value, str) and value.strip().startswith("<svg"):
                    import base64
                    svg_b64 = base64.b64encode(value.encode('utf-8')).decode('utf-8')
                    svg_items.append(
                        html.Div([
                            html.H6(label, className="fw-bold mt-3"),
                            html.Iframe(
                                src=f"data:image/svg+xml;base64,{svg_b64}",
                                style={
                                    "border": "1px solid #dee2e6",
                                    "borderRadius": "8px",
                                    "width": "100%",
                                    "maxWidth": "620px",
                                    "height": "620px",
                                    "backgroundColor": "#ffffff",
                                    "display": "block",
                                    "margin": "0 auto",
                                }
                            ),
                        ], className="mb-3")
                    )
                continue
            
            if value is None:
                value = "—"
            elif isinstance(value, float):
                value = f"{value:.3f}"
            else:
                value = str(value)
            
            result_items.append(
                html.Div([
                    html.Span(f"{label}:", style=LABEL_STYLE),
                    html.Code(value, style=VALUE_STYLE),
                ], className="mb-2")
            )
        
        # Combine text results and SVG images
        all_items = result_items + svg_items
        
        results_column = dbc.Col([
            html.H5("Results", className="fw-bold"),
            *all_items,
        ], width=12) if all_items else dbc.Col(width=0)
        
        return results_column, dbc.Col(width=0)
    
    # Original result_display format with inputs/outputs
    # Build inputs column
    input_specs = result_display.get("inputs", [])
    input_items = []
    
    for spec in input_specs:
        key = spec.get("key", "")
        label = spec.get("label", key)
        truncate = spec.get("truncate", 0)
        
        value = get_nested_value(result_data, key)
        if value is None:
            value = "—"
        else:
            value = str(value)
            if truncate > 0 and len(value) > truncate:
                value = value[:truncate] + "..."
        
        input_items.append(
            html.Div([
                html.Span(f"{label}:", style=LABEL_STYLE),
                html.Code(value, style=VALUE_STYLE),
            ], className="mb-2")
        )
    
    # Build outputs column
    output_specs = result_display.get("outputs", [])
    output_items = []
    
    for spec in output_specs:
        key = spec.get("key", "")
        label = spec.get("label", key)
        truncate = spec.get("truncate", 0)
        
        value = get_nested_value(result_data, key)
        if value is None:
            value = "—"
        else:
            value = str(value)
            if truncate > 0 and len(value) > truncate:
                value = value[:truncate] + "..."
        
        output_items.append(
            html.Div([
                html.Span(f"{label}:", style=LABEL_STYLE),
                html.Code(value, style=VALUE_STYLE),
            ], className="mb-2")
        )
    
    inputs_column = dbc.Col([
        html.H5("Inputs", className="fw-bold"),
        *input_items,
    ], width=6) if input_items else dbc.Col(width=0)
    
    outputs_column = dbc.Col([
        html.H5("Outputs", className="fw-bold"),
        *output_items,
    ], width=6) if output_items else dbc.Col(width=0)
    
    return inputs_column, outputs_column


# ---------------------------------------------------------------------------
# Generic modal layout
# ---------------------------------------------------------------------------

def build_generic_layout(
    block_name,
    schema,
    form_values,
    result_data=None,
    alert_prefix="block",
):
    """
    Build the complete modal layout for any block.
    
    Args:
        block_name: Block name
        schema: Dict with schema
        form_values: Dict with current form values
        result_data: Dict with results (optional)
        alert_prefix: Prefix for alert IDs
    
    Returns:
        Dict with {body, figure, actions}
    """
    save_alert_id = f"{alert_prefix}-save-alert"
    run_alert_id = f"{alert_prefix}-run-alert"
    
    form = build_form_from_schema(schema, form_values)
    inputs_col, outputs_col = build_result_display(schema, result_data)
    
    body = html.Div([
        dmc.Alert(id=save_alert_id, hide=True, duration=4000, title="", children=""),
        dmc.Alert(id=run_alert_id, hide=True, duration=4000, title="", children=""),
        form,
        html.Hr(className="my-4"),
        dbc.Row([inputs_col, outputs_col]),
    ])
    
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white",
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[{
            "text": f"Run {block_name} to view results.",
            "xref": "paper",
            "yref": "paper",
            "x": 0.5,
            "y": 0.5,
            "showarrow": False,
            "font": {"size": 13},
        }],
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        height=260,
    )
    
    actions = []
    for action in schema.get("actions", []):
        actions.append(
            dbc.Button(
                action.get("label", "Action"),
                id=action.get("button_id", f"{alert_prefix}-btn"),
                color=action.get("color", "primary"),
                className="me-2",
            )
        )
    
    return {
        "body": body,
        "figure": fig,
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# Generic callback registration
# ---------------------------------------------------------------------------

def register_generic_callbacks(
    app,
    block_name,
    schema,
    node_id,
    run_pipeline_fn,
    config_dir,
    serialise_for_store=None,
    update_latest_result=None,
):
    """
    Register generic callbacks for save and run.
    
    Args:
        app: Dash instance
        block_name: Block name
        schema: Dict with schema
        node_id: Node ID in diagram
        run_pipeline_fn: Function to execute pipeline
        config_dir: Configuration directory
        serialise_for_store: Function to serialize results
        update_latest_result: Function to update results in cache
    """
    from SASpipeline import get_field_ids_from_schema, apply_form_state, save_config
    
    field_ids = get_field_ids_from_schema(schema)
    
    prefix = schema.get("block_id", block_name.lower())
    save_alert_id = f"{prefix}-save-alert"
    run_alert_id = f"{prefix}-run-alert"
    save_btn_id = f"{prefix}-save-btn"
    run_btn_id = f"{prefix}-run-btn"
    result_store_id = schema.get("result_store_id", f"{prefix}-store")
    
    state_components = [State(fid, "value") for fid in field_ids]
    
    # SAVE callback
    save_outputs = (
        Output("blocks-store", "data", allow_duplicate=True),
        Output(save_alert_id, "children"),
        Output(save_alert_id, "color"),
        Output(save_alert_id, "hide"),
        Output(save_alert_id, "title"),
    )
    save_inputs = (Input(save_btn_id, "n_clicks"),)
    save_states = (
        State("selected-block-id", "data"),
        State("blocks-store", "data"),
        *state_components,
    )
    
    @app.callback(*save_outputs, *save_inputs, *save_states, prevent_initial_call=True)
    def _save_settings(n_clicks, block_id, blocks_data, *field_values):
        if not n_clicks or block_id != node_id or not blocks_data:
            return (no_update, no_update, no_update, True, no_update)
        
        try:
            form_state = dict(zip(field_ids, field_values))
            
            updated_blocks = []
            saved_params = None
            
            for blk in blocks_data:
                if blk.get("id") != node_id:
                    updated_blocks.append(blk)
                    continue
                
                params_dict = copy.deepcopy(blk.get("params", {})) if isinstance(blk.get("params"), dict) else {}
                updated_params = apply_form_state(schema, params_dict, form_state)
                saved_params = updated_params
                updated_blocks.append({**blk, "params": updated_params})
            
            if saved_params is None:
                return (no_update, f"Error: {block_name} block not found.", "red", False, "Error")
            
            save_config(block_name, saved_params)
            
            return (updated_blocks, f"{block_name} configuration saved.", "green", False, "Success")
        
        except Exception as exc:
            return (no_update, f"Error: {exc}", "red", False, "Error")
    
    # RUN callback
    run_outputs = (
        Output(run_alert_id, "children"),
        Output(run_alert_id, "color"),
        Output(run_alert_id, "hide"),
        Output(run_alert_id, "title"),
        Output(result_store_id, "data"),
    )
    run_inputs = (Input(run_btn_id, "n_clicks"),)
    run_states = (
        State("selected-block-id", "data"),
        State("blocks-store", "data"),
    )
    
    @app.callback(*run_outputs, *run_inputs, *run_states, prevent_initial_call=True)
    def _run_pipeline(n_clicks, block_id, blocks_data):
        if not n_clicks or block_id != node_id or not blocks_data:
            return (no_update, no_update, True, no_update, no_update)
        
        params = next(
            (copy.deepcopy(blk.get("params", {})) for blk in blocks_data if blk.get("id") == node_id),
            None,
        )
        
        try:
            result = run_pipeline_fn(
                config_dir=config_dir,
                config_overrides=params if isinstance(params, dict) else None,
                dry_run=False,
            )
            
            if result is None:
                raise RuntimeError(f"No result produced by {block_name} pipeline")
            
            if serialise_for_store:
                result_dict = serialise_for_store(result)
            else:
                result_dict = result if isinstance(result, dict) else {"data": result}
            
            payload = result_dict if isinstance(result_dict, dict) else {"data": result_dict}
            
            execution_id = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if isinstance(payload, dict):
                payload["executed_at"] = execution_id
            
            if update_latest_result:
                update_latest_result(payload, execution_id)
            
            return (
                f"{block_name} executed successfully.",
                "green",
                False,
                "Success",
                payload,
            )
        
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return (f"Error: {exc}", "red", False, "Error", no_update)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "VALUE_STYLE",
    "LABEL_STYLE",
    "set_config_init_params",
    "build_form_component",
    "build_form_from_schema",
    "get_nested_value",
    "build_result_display",
    "build_generic_layout",
    "register_generic_callbacks",
]
