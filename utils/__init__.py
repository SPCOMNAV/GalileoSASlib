"""
utils package
-------------
Shared utilities for all pipeline blocks.

Submodules:
    paths.py        — path constants + logging (debug, console_log)
    coerce.py       — type coercion (coerce_bool, coerce_int, …)
    io_helpers.py   — JSON I/O, serialization, schema/config helpers
    time_galileo.py — GalileoTime class + GST/GPS time conversions
    bladeAPI.py     — BladeRF SDR API client
    execution_context.py — inter-block data store (singleton)
"""

from utils.paths import (
    BLOCKS_UTILS_DIR,
    BASE_DIR,
    SCHEMA_DIR,
    CONFIG_DIR,
    DATASET_NAME,
    DATA_DIR,
    RESULTS_DIR,
    get_results_subdir,
    clear_results_subdir,
    DEBUG_MODE,
    debug,
    console_log,
    debug_log,
)

from utils.coerce import (
    coerce_bool,
    coerce_int,
    coerce_float,
    coerce_string,
    coerce_optional_string,
    coerce_path,
    coerce_list,
    coerce_dict,
    parse_bool,
)

from utils.io_helpers import (
    parse_iso_datetime,
    now_utc,
    format_timestamp,
    serialise_value,
    serialise_for_store,
    load_json_file,
    save_json_file,
    load_schema,
    load_config,
    save_config,
    get_field_ids_from_schema,
    extract_defaults_from_schema,
    extract_form_state,
    apply_form_state,
)

from utils.time_galileo import (
    GST_EPOCH,
    GPS_EPOCH,
    GST_WEEK_SECONDS,
    GST_WN_MODULO,
    GPS_UTC_LEAP_S,
    GalileoTime,
    gst_seconds_to_datetime,
    datetime_to_gst_seconds,
    gst_calendar_to_gst_seconds,
    gst_to_week_tow,
    week_tow_to_gst_seconds,
    gst_to_recs_timestamp,
    generate_recs_filename,
)

from utils.bladeAPI import BladeUIAPI, build_bladerf_config, schedule_and_download

from utils.execution_context import ExecutionContext, get_execution_context

__all__ = [
    # Paths
    "BLOCKS_UTILS_DIR",
    "BASE_DIR",
    "SCHEMA_DIR",
    "CONFIG_DIR",
    "DATASET_NAME",
    "DATA_DIR",
    "RESULTS_DIR",
    "get_results_subdir",
    "clear_results_subdir",
    # Logging
    "DEBUG_MODE",
    "debug",
    "console_log",
    "debug_log",
    # Coercion
    "coerce_bool",
    "coerce_int",
    "coerce_float",
    "coerce_string",
    "coerce_optional_string",
    "coerce_path",
    "coerce_list",
    "coerce_dict",
    "parse_bool",
    # Serialization
    "serialise_value",
    "serialise_for_store",
    # JSON I/O
    "load_json_file",
    "save_json_file",
    "load_schema",
    "load_config",
    "save_config",
    # Generic datetime
    "parse_iso_datetime",
    "now_utc",
    "format_timestamp",
    # Schema utilities
    "get_field_ids_from_schema",
    "extract_defaults_from_schema",
    "extract_form_state",
    "apply_form_state",
    # GST / Time constants
    "GST_EPOCH",
    "GPS_EPOCH",
    "GST_WEEK_SECONDS",
    "GST_WN_MODULO",
    "GPS_UTC_LEAP_S",
    # GalileoTime class
    "GalileoTime",
    # GST convenience functions
    "gst_seconds_to_datetime",
    "datetime_to_gst_seconds",
    "gst_calendar_to_gst_seconds",
    "gst_to_week_tow",
    "week_tow_to_gst_seconds",
    "gst_to_recs_timestamp",
    "generate_recs_filename",
    # BladeRF API
    "BladeUIAPI",
    "build_bladerf_config",
    "schedule_and_download",
    # Execution context
    "ExecutionContext",
    "get_execution_context",
]
