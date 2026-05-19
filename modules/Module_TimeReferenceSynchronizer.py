"""
Module_TimeReferenceSynchronizer.py
----------------------------------
Block that synchronizes with NTP servers and calculates the time offset
between NTP time and local system time.

This block sits between ConfigInit and BGDandRECSdownloader in the pipeline.
"""

from __future__ import annotations

import socket
import struct
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from utils.utils import (
    coerce_string,
    coerce_int,
    coerce_float,
    load_schema as _load_schema,
    load_config as _load_config,
    save_config as _save_config,
    console_log,
)

################################################ CONSTANTS ################################################

MODULE_FILE = "TimeReferenceSynchronizer"

# Default NTP servers from https://gist.github.com/mutin-sa/eea1c396b1e610a2da1e5550d94b0453
DEFAULT_NTP_SERVERS = [
    {"name": "Google", "address": "time.google.com"},
    {"name": "Cloudflare", "address": "time.cloudflare.com"},
    {"name": "Microsoft", "address": "time.windows.com"},
    {"name": "Apple", "address": "time.apple.com"},
    {"name": "NIST", "address": "time.nist.gov"},
    {"name": "Amazon", "address": "time.aws.com"},
    {"name": "Facebook", "address": "time.facebook.com"},
    {"name": "NTP Pool Europe", "address": "europe.pool.ntp.org"},
    {"name": "NTP Pool Global", "address": "pool.ntp.org"},
    {"name": "Hetzner", "address": "ntp1.hetzner.de"},
]

# NTP epoch: January 1, 1900 00:00:00
NTP_EPOCH = datetime(1900, 1, 1, tzinfo=timezone.utc)
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
NTP_DELTA = (UNIX_EPOCH - NTP_EPOCH).total_seconds()  # 2208988800 seconds

################################################# SCHEMA / CONFIG LOADERS #################################################

def load_schema() -> Dict[str, Any]:
    """Load the block schema from JSON."""
    return _load_schema(MODULE_FILE)


def load_config() -> Dict[str, Any]:
    """Load current config values."""
    config = _load_config(MODULE_FILE)
    return config if config else get_defaults()


def save_config(config: Dict[str, Any]) -> None:
    """Persist config values."""
    _save_config(MODULE_FILE, config)


def get_defaults() -> Dict[str, Any]:
    """Extract default values from schema."""
    schema = load_schema()
    defaults = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            field_id = field.get("id")
            if field_id and "default" in field:
                defaults[field_id] = field["default"]
    return defaults


################################################# HELPERS #################################################

def _debug(msg: str) -> None:
    """Print debug message with block name prefix."""
    console_log("TimeReferenceSynchronizer", msg)


def query_ntp_server(server: str, timeout: float = 5.0) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Query an NTP server and return the time offset.
    
    Uses a simplified NTP client implementation (SNTP).
    
    Args:
        server: NTP server hostname or IP address
        timeout: Connection timeout in seconds
        
    Returns:
        Tuple of (ntp_timestamp, offset_seconds, error_message)
        - ntp_timestamp: Unix timestamp from NTP server (None if failed)
        - offset_seconds: Time difference in seconds (NTP - local), positive means local is behind
        - error_message: Error string if failed (None if successful)
    """
    NTP_PORT = 123
    
    # NTP request packet (48 bytes)
    # Leap Indicator = 0, Version = 4, Mode = 3 (client)
    # First byte: LI (2 bits) + VN (3 bits) + Mode (3 bits) = 0b00_100_011 = 0x23
    ntp_request = b'\x23' + b'\x00' * 47
    
    try:
        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        
        # Record local time before sending
        local_time_before = time.time()
        
        # Send NTP request
        sock.sendto(ntp_request, (server, NTP_PORT))
        
        # Receive response
        response, _ = sock.recvfrom(48)
        
        # Record local time after receiving
        local_time_after = time.time()
        
        sock.close()
        
        if len(response) < 48:
            return None, None, f"Invalid response length: {len(response)}"
        
        # Extract transmit timestamp (bytes 40-47)
        # NTP timestamp: 32 bits for seconds, 32 bits for fraction
        transmit_seconds = struct.unpack('!I', response[40:44])[0]
        transmit_fraction = struct.unpack('!I', response[44:48])[0]
        
        # Convert to Unix timestamp
        ntp_timestamp = (transmit_seconds - NTP_DELTA) + (transmit_fraction / (2**32))
        
        # Calculate round-trip time and estimate local time at reception
        round_trip = local_time_after - local_time_before
        local_time_estimate = local_time_before + (round_trip / 2)
        
        # Calculate offset: positive = local clock is behind NTP
        offset = ntp_timestamp - local_time_estimate
        
        return ntp_timestamp, offset, None
        
    except socket.timeout:
        return None, None, f"Connection timeout to {server}"
    except socket.gaierror as e:
        return None, None, f"DNS resolution failed for {server}: {e}"
    except Exception as e:
        return None, None, f"Error querying {server}: {e}"


def get_available_ntp_servers() -> List[Dict[str, str]]:
    """
    Get the list of available NTP servers.
    
    Returns:
        List of dicts with 'name' and 'address' keys
    """
    return DEFAULT_NTP_SERVERS.copy()


################################################# Configuration validation ##############################################################

def validate_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and coerce configuration values."""
    defaults = get_defaults()
    
    return {
        "selected_ntp_server": coerce_string(
            config.get("selected_ntp_server"),
            defaults.get("selected_ntp_server", "time.google.com")
        ),
        "query_timeout_sec": coerce_float(
            config.get("query_timeout_sec"),
            defaults.get("query_timeout_sec", 5.0),
            minimum=1.0,
            maximum=30.0
        ),
        "retry_count": coerce_int(
            config.get("retry_count"),
            defaults.get("retry_count", 3),
            minimum=1,
            maximum=10
        ),
    }


################################################# Pipeline execution ##############################################################

def _run_pipeline_legacy(config: Optional[Mapping[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """
    Run the Time Reference Synchronizer pipeline.
    
    This block queries the configured NTP server and calculates the time offset
    between the NTP server time and the local system time.
    
    Args:
        config: Optional configuration dict. If None, loads from file.
        **kwargs: Extra arguments (ignored, for compatibility with pipeline)
        
    Returns:
        Dict with:
            - status: "success" or "error"
            - message: Human-readable status message
            - ntp_server: The NTP server that was queried
            - ntp_timestamp: Unix timestamp from NTP server
            - ntp_datetime: ISO format datetime from NTP
            - local_timestamp: Local Unix timestamp
            - local_datetime: ISO format local datetime
            - offset_seconds: Time offset in seconds (NTP - local)
            - offset_ms: Time offset in milliseconds
            - offset_readable: Human-readable offset string
    """
    # Load config if not provided
    if config is None:
        config = load_config()
    
    # Validate and coerce values
    validated = validate_config(config)
    
    # Save validated config
    save_config(validated)
    
    ntp_server = validated["selected_ntp_server"]
    timeout = validated["query_timeout_sec"]
    retry_count = validated["retry_count"]
    
    _debug(f"Querying NTP server: {ntp_server}")
    
    # Try to query the NTP server with retries
    last_error = None
    for attempt in range(retry_count):
        ntp_timestamp, offset, error = query_ntp_server(ntp_server, timeout)
        
        if error is None and offset is not None:
            # Success!
            local_timestamp = time.time()
            local_dt = datetime.fromtimestamp(local_timestamp, tz=timezone.utc)
            ntp_dt = datetime.fromtimestamp(ntp_timestamp, tz=timezone.utc)
            
            # Format offset
            offset_ms = offset * 1000
            if abs(offset) < 1:
                offset_readable = f"{offset_ms:.2f} ms"
            elif abs(offset) < 60:
                offset_readable = f"{offset:.3f} seconds"
            elif abs(offset) < 3600:
                offset_readable = f"{offset/60:.2f} minutes"
            else:
                offset_readable = f"{offset/3600:.2f} hours"
            
            direction = "behind" if offset > 0 else "ahead of"
            
            _debug(f"NTP sync successful. Local clock is {offset_readable} {direction} NTP time")
            
            return {
                "status": "success",
                "message": f"Successfully synchronized with {ntp_server}",
                "ntp_server": ntp_server,
                "ntp_timestamp": ntp_timestamp,
                "ntp_datetime": ntp_dt.isoformat(),
                "local_timestamp": local_timestamp,
                "local_datetime": local_dt.isoformat(),
                "offset_seconds": offset,
                "offset_ms": offset_ms,
                "offset_readable": offset_readable,
                "clock_status": f"Local clock is {offset_readable} {direction} NTP time",
            }
        
        last_error = error
        _debug(f"Attempt {attempt + 1}/{retry_count} failed: {error}")
        
        if attempt < retry_count - 1:
            time.sleep(0.5)  # Brief delay between retries
    
    # All retries failed
    _debug(f"Failed to sync with {ntp_server} after {retry_count} attempts")
    
    return {
        "status": "error",
        "message": f"Failed to query NTP server {ntp_server}: {last_error}",
        "ntp_server": ntp_server,
        "ntp_timestamp": None,
        "ntp_datetime": None,
        "local_timestamp": time.time(),
        "local_datetime": datetime.now(timezone.utc).isoformat(),
        "offset_seconds": None,
        "offset_ms": None,
        "offset_readable": "N/A",
        "clock_status": f"Failed to synchronize: {last_error}",
    }


############################################ PIPELINE EXECUTION (CONTRACT 1.0) ############################################

def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point. consumes=[], produces=[] — pure side-effect
    block (NTP query) whose values are reported via summary/data only."""
    raw = _run_pipeline_legacy(config=config)
    return {
        "outputs": {},
        "summary": {
            "status": raw.get("status"),
            "ntp_server": raw.get("ntp_server"),
            "offset_ms": raw.get("offset_ms"),
            "clock_status": raw.get("clock_status"),
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


######################################################## EXPORTS ########################################################

__all__ = [
    "load_schema",
    "load_config",
    "save_config",
    "get_defaults",
    "validate_config",
    "run_pipeline",
    "query_ntp_server",
    "get_available_ntp_servers",
    "DEFAULT_NTP_SERVERS",
]
