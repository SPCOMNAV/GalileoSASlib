from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from utils.utils import (
    load_json_file,
    save_json_file,
    console_log,
    serialise_value,
    parse_iso_datetime,
    BASE_DIR,
    SCHEMA_DIR,
    CONFIG_DIR,
    DATA_DIR,
)

# Import from dedicated BladeAPI module
from utils.bladeAPI import (
    BladeUIAPI,
    build_bladerf_config,
    schedule_and_download,
)

############################################ CONSTANTS ############################################

DEFAULT_OUTPUT_DIR = DATA_DIR / "Snapshots_IQ"
CONNECTION_TIMEOUT_SECONDS = 15  # Timeout for server connection check
PENDING_RECORDINGS_FILE = CONFIG_DIR / "pending_recordings.json"

######################################### Schema / Config loaders #########################################

def load_schema() -> Dict[str, Any]:
    """Load the block schema from JSON."""
    return load_json_file(SCHEMA_DIR / "SnapshotRecording_schema.json")


def load_defaults() -> Dict[str, Any]:
    """Extract default values from schema."""
    schema = load_schema()
    defaults = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            field_id = field.get("id")
            if field_id:
                defaults[field_id] = field.get("default")
    return defaults


def load_config() -> Dict[str, Any]:
    """Load current config values (merged with defaults)."""
    defaults = load_defaults()
    config = load_json_file(CONFIG_DIR / "SnapshotRecording.json")
    defaults.update(config)
    return defaults


def save_config(config: Dict[str, Any]) -> None:
    """Persist config values."""
    save_json_file(CONFIG_DIR / "SnapshotRecording.json", config)


######################################### Pending recordings management #########################################

def load_pending_recordings() -> List[Dict[str, Any]]:
    """Load list of pending recordings awaiting download."""
    if PENDING_RECORDINGS_FILE.exists():
        return load_json_file(PENDING_RECORDINGS_FILE)
    return []


def save_pending_recordings(recordings: List[Dict[str, Any]]) -> None:
    """Save list of pending recordings."""
    save_json_file(PENDING_RECORDINGS_FILE, recordings)


def add_pending_recording(recording: Dict[str, Any]) -> None:
    """Add a recording to the pending list."""
    recordings = load_pending_recordings()
    recordings.append(recording)
    save_pending_recordings(recordings)


def remove_pending_recording(scheduled_time: str) -> None:
    """Remove a recording from pending list by scheduled time."""
    recordings = load_pending_recordings()
    recordings = [r for r in recordings if r.get("scheduled_time") != scheduled_time]
    save_pending_recordings(recordings)


######################################### Helper functions #########################################

def _debug(msg: str) -> None:
    """Print debug message with block name prefix."""
    console_log("SnapshotRecording", msg)


def check_server_available(
    api_base_url: str,
    api_username: str,
    api_password: str,
    timeout: float = CONNECTION_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Check if the BladeRF server is available and reachable.
    
    Args:
        api_base_url: Server URL
        api_username: Username
        api_password: Password
        timeout: Connection timeout in seconds
        
    Returns:
        Dict with 'available' bool and 'error' message if not available
    """
    import socket
    from urllib.parse import urlparse
    
    try:
        # Parse URL to get host and port
        parsed = urlparse(api_base_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        
        # Quick socket check
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result != 0:
            return {
                "available": False,
                "error": f"Server not reachable at {host}:{port} (connection refused)"
            }
        
        # Try actual login
        api = BladeUIAPI(api_base_url, api_username, api_password)
        api.DEFAULT_TIMEOUT = timeout
        api.login()
        api.logout()
        
        return {"available": True, "error": None}
        
    except socket.timeout:
        return {
            "available": False,
            "error": f"Connection timeout ({timeout}s) - server not responding"
        }
    except Exception as e:
        return {
            "available": False,
            "error": f"Connection error: {e}"
        }


def _log_factory(logger: Optional[Callable[[str], None]]) -> Callable[[str], None]:
    """Create a logger function or return no-op if None provided."""
    if logger is None:
        return lambda _msg: None
    return logger


def _get_output_dir(config: Dict[str, Any]) -> Path:
    """Get output directory from config, using execution-specific directory if available."""
    output_dir_raw = config.get("output_dir")
    if output_dir_raw:
        output_dir = Path(output_dir_raw)
        if not output_dir.is_absolute():
            output_dir = (BASE_DIR / output_dir).resolve()
    else:
        # Use execution-specific results directory if available
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        if ctx.get_execution_id():
            output_dir = ctx.get_execution_results_dir("snapshots")
        else:
            output_dir = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _parse_scheduled_time(config: Dict[str, Any]) -> datetime:
    """Parse scheduled_time from config or return current time."""
    scheduled_str = config.get("scheduled_time", "")
    if scheduled_str:
        parsed = parse_iso_datetime(scheduled_str)
        if parsed:
            return parsed
    return datetime.now(timezone.utc)


##################################################### Schedule Recording #####################################################

def schedule_recording(
    config: Optional[Mapping[str, Any]] = None,
    *,
    logger: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Schedule a recording on the BladeRF server.
    
    This sends the recording request and returns immediately.
    The recording details are saved to pending_recordings.json for later download.
    
    Note: This function assumes SDR mode. Manual mode check should be done
    in run_pipeline() before calling this function.
    
    Args:
        config: Recording configuration
        logger: Optional logging function
        
    Returns:
        Dict with scheduling status and details
    """
    _log = _log_factory(logger)
    data = dict(config) if config else load_config()
    
    # API connection
    api_base_url = data.get("api_base_url", "")
    api_username = data.get("api_username", "")
    api_password = data.get("api_password", "")
    
    if not all([api_base_url, api_username, api_password]):
        return {
            "status": "error",
            "error": "API credentials not configured",
        }
    
    # Check server availability first (fast fail)
    _log(f"Checking server availability at {api_base_url}...")
    server_check = check_server_available(api_base_url, api_username, api_password)
    if not server_check["available"]:
        _log(f"Server not available: {server_check['error']}")
        return {
            "status": "error",
            "error": f"Server not available: {server_check['error']}",
        }
    _log("Server is available")
    
    # Parse timing
    record_now = data.get("record_now", False)
    scheduled_time = _parse_scheduled_time(data)
    duration_seconds = float(data.get("duration_seconds", 1.0))
    
    # Build BladeRF config
    bladerf_config = build_bladerf_config(
        frequency_e6=int(data.get("frequency_e6", 1278750000)),
        frequency_e1=int(data.get("frequency_e1", 1575420000)),
        sample_rate=int(data.get("sample_rate", 20000000)),
        bandwidth=int(data.get("bandwidth", 10000000)),
        gain=int(data.get("gain", 60)),
        duration_seconds=duration_seconds,
        scheduled_time=scheduled_time,
        record_now=record_now,
        bias_tee_blade1=data.get("bias_tee_blade1", True),
        bias_tee_blade2=data.get("bias_tee_blade2", True),
        gain_mode=data.get("gain_mode", "Default"),
        agc_enabled=data.get("agc_enabled", False),
        get_tesla_keys=data.get("get_tesla_keys", False),
        selected_devices=data.get("selected_devices", ["BLADE_1", "BLADE_2"]),
    )
    
    _log(f"Connecting to BladeRF API at {api_base_url}...")
    
    try:
        api = BladeUIAPI(api_base_url, api_username, api_password)
        api.login()
        _log("Authenticated successfully")
        
        response = api.request_recording(bladerf_config)
        _log(f"Recording scheduled: {response}")
        
        api.logout()
    except Exception as e:
        _log(f"ERROR: Recording failed after authentication: {e}")
        return {
            "status": "error",
            "error": f"API error: {e}",
        }
    
    # Calculate when recording will be ready
    if record_now:
        ready_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds + 5)
    else:
        ready_time = scheduled_time + timedelta(seconds=duration_seconds + 5)
    
    # Save to pending recordings
    pending_record = {
        "scheduled_time": scheduled_time.isoformat(),
        "ready_time": ready_time.isoformat(),
        "duration_seconds": duration_seconds,
        "api_base_url": api_base_url,
        "api_username": api_username,
        "api_password": api_password,
        "output_dir": str(_get_output_dir(data)),
        "poll_interval_seconds": float(data.get("poll_interval_seconds", 10.0)),
        "download_timeout_minutes": float(data.get("download_timeout_minutes", 30.0)),
        "config": {k: v for k, v in data.items() if k not in ["api_password"]},
    }
    add_pending_recording(pending_record)
    
    _log(f"Recording will be ready at: {ready_time.isoformat()}")
    
    return {
        "status": "scheduled",
        "scheduled_time": scheduled_time.isoformat(),
        "ready_time": ready_time.isoformat(),
        "duration_seconds": duration_seconds,
        "api_response": response,
        "manual_mode": False,
    }


##################################################### Download Recording #####################################################

def download_recording(
    config: Optional[Mapping[str, Any]] = None,
    *,
    scheduled_time: Optional[str] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Download a previously scheduled recording.
    
    Polls the API until the recording is ready, then downloads all files.
    
    Args:
        config: Recording configuration (optional if scheduled_time provided)
        scheduled_time: ISO timestamp of the scheduled recording
        logger: Optional logging function
        
    Returns:
        Dict with download status and file paths
    """
    _log = _log_factory(logger)
    
    # Find the pending recording
    if scheduled_time:
        recordings = load_pending_recordings()
        pending = next((r for r in recordings if r.get("scheduled_time") == scheduled_time), None)
        if not pending:
            return {"status": "error", "error": f"No pending recording found for {scheduled_time}"}
        data = pending
    elif config:
        data = dict(config)
    else:
        return {"status": "error", "error": "No config or scheduled_time provided"}
    
    api_base_url = data.get("api_base_url", "")
    api_username = data.get("api_username", "")
    api_password = data.get("api_password", "")
    output_dir = Path(data.get("output_dir", str(DEFAULT_OUTPUT_DIR)))
    poll_interval = float(data.get("poll_interval_seconds", 10.0))
    timeout_minutes = float(data.get("download_timeout_minutes", 30.0))
    
    record_time_str = data.get("scheduled_time", "")
    if not record_time_str:
        return {"status": "error", "error": "No scheduled_time in config"}
    
    record_time = parse_iso_datetime(record_time_str)
    if not record_time:
        return {"status": "error", "error": f"Invalid scheduled_time: {record_time_str}"}
    
    _log(f"Checking recording status for {record_time_str}...")
    
    try:
        api = BladeUIAPI(api_base_url, api_username, api_password)
        api.login()
        
        timeout = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
        downloaded_files = []
        
        while datetime.now(timezone.utc) < timeout:
            # Use find_recent_recordings to search within a time window
            # This handles cases where actual recording time differs from scheduled time
            status = api.find_recent_recordings(record_time, tolerance_seconds=120)
            _log(f"Recording state: {status.get('records_state', 'unknown')}")
            
            if status.get("records_state") == "Completed":
                records = status.get("records", [])
                output_dir.mkdir(parents=True, exist_ok=True)
                
                for record in records:
                    remote_path = record.get("path", "")
                    device_name = record.get("device_name", "unknown")
                    local_filename = Path(remote_path).name
                    local_path = output_dir / local_filename
                    
                    _log(f"Downloading {device_name}: {remote_path}")
                    try:
                        api.download(f"/{remote_path}", local_path)
                        downloaded_files.append(str(local_path))
                    except Exception as dl_err:
                        _log(f"Download failed for {device_name}: {dl_err}")
                        # Remove partial file
                        if local_path.exists():
                            local_path.unlink()
                        continue
                
                break
            
            _log(f"Recording not ready, waiting {poll_interval}s...")
            time.sleep(poll_interval)
        
        api.logout()
        
        if not downloaded_files:
            return {"status": "timeout", "error": "Recording did not complete in time"}
        
        # Remove from pending
        remove_pending_recording(record_time_str)
        
        return {
            "status": "downloaded",
            "downloaded_files": downloaded_files,
            "output_dir": str(output_dir),
            "manual_mode": False,
        }
        
    except Exception as e:
        return {"status": "error", "error": f"Download error: {e}"}


##################################################### Check and Download Ready Recordings #####################################################

def check_and_download_ready(
    *,
    logger: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Check all pending recordings and download any that are ready.
    
    This should be called periodically (e.g., by a scheduler or timer).
    Only attempts download for recordings whose ready_time has passed.
    
    Returns:
        List of download results for each processed recording
    """
    _log = _log_factory(logger)
    recordings = load_pending_recordings()
    results = []
    now = datetime.now(timezone.utc)
    
    for rec in recordings:
        ready_time_str = rec.get("ready_time", "")
        ready_time = parse_iso_datetime(ready_time_str)
        
        if ready_time and now >= ready_time:
            _log(f"Recording ready, starting download: {rec.get('scheduled_time')}")
            result = download_recording(
                config=rec,
                scheduled_time=rec.get("scheduled_time"),
                logger=logger,
            )
            results.append(result)
    
    return results


##################################################### Background scheduler thread #####################################################

_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop_event = threading.Event()


def start_download_scheduler(
    check_interval_seconds: float = 60.0,
    logger: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Start a background thread that periodically checks for ready recordings.
    
    Args:
        check_interval_seconds: How often to check for ready recordings
        logger: Optional logging function
    """
    global _scheduler_thread, _scheduler_stop_event
    
    if _scheduler_thread and _scheduler_thread.is_alive():
        return  # Already running
    
    _scheduler_stop_event.clear()
    
    def scheduler_loop():
        _log = _log_factory(logger)
        while not _scheduler_stop_event.is_set():
            try:
                results = check_and_download_ready(logger=logger)
                if results:
                    _log(f"Downloaded {len(results)} recordings")
            except Exception as e:
                _log(f"Scheduler error: {e}")
            
            _scheduler_stop_event.wait(check_interval_seconds)
    
    _scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    _scheduler_thread.start()


def stop_download_scheduler() -> None:
    """Stop the background download scheduler."""
    global _scheduler_thread, _scheduler_stop_event
    _scheduler_stop_event.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=5.0)
        _scheduler_thread = None


##################################################### Full Pipeline Execution #####################################################

def _run_pipeline_legacy(
    config: Optional[Mapping[str, Any]] = None,
    *,
    logger: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
    manual_mode: Optional[bool] = None,
    snapshot_path_e1: str = "",
    snapshot_path_e6: str = "",
    record_now: Optional[bool] = None,
    scheduled_time: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    Run the full snapshot recording pipeline (blocking).
    
    1. If manual_mode: return existing file paths (from ConfigInit)
    2. Otherwise: schedule recording, wait until ready, download files
    
    Args:
        config: Recording configuration (block-specific settings)
        logger: Optional logging function
        dry_run: If True, don't actually record
        manual_mode: If True, use existing files (from ConfigInit.rf_input_from_file)
        snapshot_path_e1: Path to E1 snapshot file (from ConfigInit.rf_input_path_e1)
        snapshot_path_e6: Path to E6 snapshot file (from ConfigInit.rf_input_path_e6)
        record_now: If True, record immediately (from ConfigInit.record_now)
        scheduled_time: When to record if not now (from ConfigInit.scheduled_time)
        
    Returns:
        Dict with recording results
    """
    _log = _log_factory(logger)
    data = dict(config) if config else load_config()
    
    # Override config values with explicit parameters from ConfigInit
    if record_now is not None:
        data["record_now"] = record_now
    if scheduled_time:
        data["scheduled_time"] = scheduled_time
    # Propagate duration_seconds from pipeline kwargs (e.g. auth_duration_sec)
    if "duration_seconds" in kwargs:
        data["duration_seconds"] = float(kwargs["duration_seconds"])
        _log(f"Duration overridden by pipeline: {data['duration_seconds']}s")
    
    # Determine manual mode: explicit parameter > config value > default True
    use_manual_mode = manual_mode if manual_mode is not None else data.get("manual_mode", True)
    
    # Manual mode - use existing files from ConfigInit
    if use_manual_mode:
        _log("Manual mode enabled (rf_input_from_file=True)")
        
        # Resolve E1 path
        e1_path = None
        if snapshot_path_e1:
            e1_path = Path(snapshot_path_e1)
            if not e1_path.is_absolute():
                e1_path = (BASE_DIR / e1_path).resolve()
            if e1_path.exists():
                _log(f"  E1 snapshot: {e1_path}")
            else:
                _log(f"  E1 snapshot NOT FOUND: {e1_path}")
                e1_path = None
        
        # Resolve E6 path
        e6_path = None
        if snapshot_path_e6:
            e6_path = Path(snapshot_path_e6)
            if not e6_path.is_absolute():
                e6_path = (BASE_DIR / e6_path).resolve()
            if e6_path.exists():
                _log(f"  E6 snapshot: {e6_path}")
            else:
                _log(f"  E6 snapshot NOT FOUND: {e6_path}")
                e6_path = None
        
        # Return paths - SASpipeline will store in ExecutionContext
        return {
            "status": "manual",
            "manual_mode": True,
            "snapshot_path_e1": str(e1_path) if e1_path else None,
            "snapshot_path_e6": str(e6_path) if e6_path else None,
            "downloaded_files": [],
        }
    
    # SDR Recording mode - return sample_rate in result for SASpipeline to store
    sample_rate = float(data.get("sample_rate", 20000000))
    _log(f"SDR mode - sample_rate_hz={sample_rate}")
    
    if dry_run:
        _log("Dry run - skipping actual recording")
        return {
            "status": "dry_run",
            "manual_mode": False,
            "sample_rate_hz": sample_rate,
            "downloaded_files": [],
        }
    
    # Schedule recording
    _log("Scheduling recording...")
    schedule_result = schedule_recording(config=data, logger=logger)
    
    if schedule_result.get("status") == "error":
        _log(f"Snapshot scheduling failed: {schedule_result.get('error', 'unknown')}")
        return schedule_result
    
    # Wait until ready time
    ready_time_str = schedule_result.get("ready_time", "")
    ready_time = parse_iso_datetime(ready_time_str)
    wake_before = float(data.get("wake_before_minutes", 5.0))
    
    if ready_time:
        wake_time = ready_time - timedelta(minutes=wake_before)
        now = datetime.now(timezone.utc)
        
        if wake_time > now:
            wait_seconds = (wake_time - now).total_seconds()
            _log(f"Sleeping for {wait_seconds:.0f}s until {wake_time.isoformat()}")
            time.sleep(wait_seconds)
    
    # Download
    _log("Starting download...")
    download_result = download_recording(
        scheduled_time=schedule_result.get("scheduled_time"),
        logger=logger,
    )
    
    return {
        **download_result,
        "scheduled_time": schedule_result.get("scheduled_time"),
        "ready_time": ready_time_str,
    }


############################################ PIPELINE EXECUTION (CONTRACT 1.0) ############################################

from utils.snapshot_naming import (
    parse_scheduled_time_doy as _parse_doy_v2,
    extract_datetime_from_snapshot_filename as _snap_dt_v2,
)
from utils.time_galileo import datetime_to_gst_seconds as _dt2gst


def _identify_e1_e6(downloaded: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Pick the E1 and E6 paths from a list of downloaded snapshot files."""
    import re as _re
    e1, e6 = None, None
    for fpath in downloaded:
        fname = Path(fpath).name
        if _re.search(r'[_\-]E1[_\-\.s]', fname, _re.IGNORECASE):
            e1 = fpath
        elif _re.search(r'[_\-]E6[_\-\.s]', fname, _re.IGNORECASE):
            e6 = fpath
        elif "blade_2" in fname.lower() or "blade2" in fname.lower():
            e1 = fpath
        elif "blade_1" in fname.lower() or "blade1" in fname.lower():
            e6 = fpath
    if e1 is None and e6 is None and downloaded:
        # Legacy fallback: first→E1, second→E6
        e1 = downloaded[0]
        e6 = downloaded[1] if len(downloaded) > 1 else None
    return e1, e6


def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: (none — all upstream values come from globals)
    produces: snapshot_path_e1, snapshot_path_e6, sample_rate_hz, snapshot_gst_sec
    """
    g = dict(globals or {})
    record_now, scheduled_iso = _parse_doy_v2(g.get("scheduled_time_doy") or "0")

    raw = _run_pipeline_legacy(
        config=config,
        logger=logger,
        manual_mode=bool(g.get("rf_input_from_file", True)),
        snapshot_path_e1=g.get("rf_input_path_e1") or "",
        snapshot_path_e6=g.get("rf_input_path_e6") or "",
        record_now=record_now,
        scheduled_time=scheduled_iso,
        duration_seconds=float(g.get("auth_duration_sec") or 1.0),
    )

    snap_e1 = raw.get("snapshot_path_e1") if isinstance(raw, dict) else None
    snap_e6 = raw.get("snapshot_path_e6") if isinstance(raw, dict) else None

    # SDR mode: identify E1/E6 from the downloaded list
    if not (snap_e1 or snap_e6):
        downloaded = (raw.get("downloaded_files") or []) if isinstance(raw, dict) else []
        if downloaded:
            snap_e1, snap_e6 = _identify_e1_e6(downloaded)

    # Derive snapshot_gst_sec from whichever path carries the YYYYMMDD_HHMMSSZ token
    gst_sec: Optional[float] = None
    for p in (snap_e6, snap_e1):
        if not p:
            continue
        snap_dt = _snap_dt_v2(p)
        if snap_dt:
            gst_sec = float(_dt2gst(snap_dt))
            break

    sample_rate_hz = raw.get("sample_rate_hz") if isinstance(raw, dict) else None

    return {
        "outputs": {
            "snapshot_path_e1": snap_e1,
            "snapshot_path_e6": snap_e6,
            "sample_rate_hz": sample_rate_hz,
            "snapshot_gst_sec": gst_sec,
        },
        "summary": {
            "status": raw.get("status") if isinstance(raw, dict) else None,
            "manual_mode": raw.get("manual_mode") if isinstance(raw, dict) else None,
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


def run_pipeline_from_json(payload: Union[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Execute the snapshot pipeline from a JSON-compatible payload."""
    if isinstance(payload, str):
        try:
            config_map = json.loads(payload.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload for snapshot pipeline") from exc
    elif isinstance(payload, Mapping):
        config_map = payload
    else:
        raise TypeError("Snapshot payload must be a mapping or JSON string")

    return run_pipeline(config_map)


################################################# Config extraction from Module_ConfigInit ##############################################################

__all__ = [
    "load_schema",
    "load_config",
    "save_config",
    "load_pending_recordings",
    "schedule_recording",
    "download_recording",
    "check_and_download_ready",
    "start_download_scheduler",
    "stop_download_scheduler",
    "run_pipeline",
    "run_pipeline_from_json",
]
