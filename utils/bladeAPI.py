"""
BladeRF API Client for SDR recording control.

This module provides the BladeUIAPI class for interacting with the BladeRF
recording server, and helper functions for building recording configurations.
"""

from __future__ import annotations

import os
import time
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Disable SSL warnings for self-signed certificates
try:
    requests.packages.urllib3.disable_warnings(
        requests.packages.urllib3.exceptions.InsecureRequestWarning
    )
except Exception:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# BladeRF API Client
# ─────────────────────────────────────────────────────────────────────────────

class BladeUIAPI:
    """
    Client for the BladeRF recording server API.
    
    Handles authentication, recording requests, status checks, and file downloads.
    
    Example:
        api = BladeUIAPI("https://192.168.1.100:443", "user", "pass")
        api.login()
        config = build_bladerf_config(record_now=True, duration_seconds=2.0)
        api.request_recording(config)
        api.logout()
    """
    
    DEFAULT_TIMEOUT = 60
    DOWNLOAD_TIMEOUT = 600
    
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self._last_error: Optional[str] = None
    
    @property
    def is_authenticated(self) -> bool:
        """Check if we have valid tokens."""
        return self.access_token is not None
    
    def login(self) -> bool:
        """Authenticate with the server and obtain tokens."""
        url = f"{self.base_url}/api/token/"
        payload = {'username': self.username, 'password': self.password}
        resp = requests.post(url, json=payload, verify=False, timeout=self.DEFAULT_TIMEOUT)
        resp.raise_for_status()
        tokens = resp.json()
        self.access_token = tokens['access']
        self.refresh_token = tokens['refresh']
        return True

    def refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            return False
        url = f"{self.base_url}/api/token/refresh/"
        payload = {'refresh': self.refresh_token}
        resp = requests.post(url, json=payload, verify=False, timeout=self.DEFAULT_TIMEOUT)
        resp.raise_for_status()
        self.access_token = resp.json()['access']
        return True

    def call_api(
        self,
        endpoint: str,
        method: str = 'GET',
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        auto_refresh: bool = True,
        timeout: Optional[int] = None,
    ) -> Any:
        """Make an authenticated API call."""
        url = f"{self.base_url}{endpoint}"
        headers = {'Authorization': f'Bearer {self.access_token}'} if self.access_token else {}
        req_timeout = timeout or self.DEFAULT_TIMEOUT
        
        resp = requests.request(
            method, url, headers=headers, json=data, params=params,
            verify=False, timeout=req_timeout
        )
        
        # Retry once if token expired
        if resp.status_code == 401 and auto_refresh and self.refresh_token:
            self.refresh_access_token()
            headers['Authorization'] = f'Bearer {self.access_token}'
            resp = requests.request(
                method, url, headers=headers, json=data, params=params,
                verify=False, timeout=req_timeout
            )
        
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return resp.text
    
    def request_recording(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Request a new recording."""
        return self.call_api(
            endpoint='/api/request-record/',
            method='POST',
            data=config
        )
    
    def check_recording_status(self, record_time: datetime) -> Dict[str, Any]:
        """Check if a recording is ready for download."""
        time_str = record_time.strftime('%Y-%m-%d %H:%M:%S')
        return self.call_api(
            "/api/records-by-date/",
            method='GET',
            params={'date': time_str}
        )
    
    def find_recent_recordings(
        self, 
        after_time: datetime, 
        tolerance_seconds: int = 120
    ) -> Dict[str, Any]:
        """
        Find recordings within a time window after the specified time.
        
        Since the exact recording timestamp may differ from the scheduled time,
        this method searches for recordings completed within a tolerance window.
        
        Args:
            after_time: The earliest expected recording time (UTC)
            tolerance_seconds: How many seconds after to search (default 120s = 2 min)
            
        Returns:
            Dict with 'records_state' and 'records' list similar to check_recording_status
        """
        from datetime import timezone
        
        all_records = self.list_recordings()
        
        # Calculate time window
        window_start = after_time
        window_end = after_time + timedelta(seconds=tolerance_seconds)
        
        matching_records = []
        for rec in all_records:
            rec_datetime_str = rec.get("datetime", "")
            if not rec_datetime_str:
                continue
            
            # Parse the datetime (format: 2026-02-08T12:07:17.010461Z)
            try:
                # Handle microseconds and Z suffix
                if rec_datetime_str.endswith('Z'):
                    rec_datetime_str = rec_datetime_str[:-1] + '+00:00'
                rec_dt = datetime.fromisoformat(rec_datetime_str)
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            
            # Check if within window
            if window_start <= rec_dt <= window_end:
                matching_records.append(rec)
        
        if matching_records:
            return {
                "records_state": "Completed",
                "records": matching_records,
            }
        return {
            "records_state": "Not Found",
            "records": [],
        }
    
    def list_recordings(self) -> List[Dict[str, Any]]:
        """List all available recordings on the server."""
        return self.call_api(endpoint='/api/record-list/')
        
    def download(
        self,
        path: str,
        dest_file: Union[str, Path],
        progress_callback: Optional[callable] = None,
    ) -> Path:
        """Download a recording file from the server."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {'Authorization': f'Bearer {self.access_token}'} if self.access_token else {}
        
        dest_path = Path(dest_file)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        import time as _time
        _dl_start = _time.monotonic()
        _dl_total_timeout = self.DOWNLOAD_TIMEOUT  # total wall-clock timeout
        with requests.get(
            url, headers=headers, stream=True, verify=False, timeout=(30, 120)
        ) as resp:
            resp.raise_for_status()
            total_size = int(resp.headers.get('content-length', 0))
            downloaded = 0
            
            with open(dest_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size:
                        progress_callback(downloaded, total_size)
                    # Abort if total download time exceeded
                    if (_time.monotonic() - _dl_start) > _dl_total_timeout:
                        raise TimeoutError(f"Download exceeded {_dl_total_timeout}s wall-clock limit ({downloaded}/{total_size} bytes)")
        
        return dest_path

    def logout(self) -> bool:
        """Logout and invalidate tokens."""
        if not self.refresh_token:
            return True
        url = f"{self.base_url}/api/logout/"
        payload = {'refresh': self.refresh_token}
        headers = {'Authorization': f'Bearer {self.access_token}'} if self.access_token else {}
        try:
            resp = requests.post(url, json=payload, headers=headers, verify=False, timeout=self.DEFAULT_TIMEOUT)
            self.access_token = None
            self.refresh_token = None
            return resp.status_code == 200
        except requests.RequestException:
            return False
    
    def __enter__(self) -> 'BladeUIAPI':
        """Context manager entry - login."""
        self.login()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - logout."""
        self.logout()


# ─────────────────────────────────────────────────────────────────────────────
# Configuration Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_bladerf_config(
    frequency_e6: int = 1278750000,
    frequency_e1: int = 1575420000,
    sample_rate: int = 20000000,
    bandwidth: int = 10000000,
    gain: int = 60,
    duration_seconds: float = 1.0,
    scheduled_time: Optional[datetime] = None,
    record_now: bool = False,
    bias_tee_blade1: bool = True,
    bias_tee_blade2: bool = True,
    gain_mode: str = 'Default',
    agc_enabled: bool = False,
    get_tesla_keys: bool = False,
    selected_devices: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build the configuration dict for BladeRF recording.
    
    Args:
        frequency_e6: E6 band center frequency in Hz (BLADE_1, default 1278.75 MHz)
        frequency_e1: E1 band center frequency in Hz (BLADE_2, default 1575.42 MHz)
        sample_rate: Sample rate in samples per second (default 20 Msps)
        bandwidth: Bandwidth in Hz (default 10 MHz)
        gain: Gain in dB (default 60)
        duration_seconds: Recording duration in seconds
        scheduled_time: When to record (None = use record_now)
        record_now: If True, record immediately
        bias_tee_blade1: Enable bias tee on BLADE_1 (for active antenna)
        bias_tee_blade2: Enable bias tee on BLADE_2
        gain_mode: 'Default', 'Manual', 'FastAttack_AGC', 'SlowAttack_AGC', 'Hybrid_AGC'
        agc_enabled: Enable automatic gain control
        get_tesla_keys: Also capture TESLA keys
        selected_devices: List of devices to use (default: ['BLADE_1', 'BLADE_2'])
    """
    num_samples = int(sample_rate * duration_seconds)
    
    record_at = ""
    if scheduled_time and not record_now:
        record_at = scheduled_time.strftime('%Y-%m-%dT%H:%M:%S')
    
    if selected_devices is None:
        selected_devices = ['BLADE_1', 'BLADE_2']
    
    def _build_param(param_type: str, value: Any, unit: Optional[str] = None, 
                     range_min: Optional[int] = None, range_max: Optional[int] = None,
                     step: int = 1, scale: int = 1, options: Optional[List] = None) -> Dict[str, Any]:
        """Build a parameter dict in the new API format."""
        param = {'type': param_type, 'value': value}
        if unit:
            param['unit'] = unit
        if range_min is not None and range_max is not None:
            param['range'] = {'min': range_min, 'max': range_max, 'step': step, 'scale': scale}
        if options is not None:
            param['options'] = options
        return param
    
    # Build a parametrised device entry per selected device.
    # First device → E6 frequency / bias_blade1.
    # Second device → E1 frequency / bias_blade2.
    # Subsequent devices reuse the E1 settings.
    def _device_entry(name: str, freq: int, bias: bool) -> Dict[str, Any]:
        return {
            'device_name': name,
            'params': {
                'frequency': _build_param('num', freq, 'Hz', 70000000, 6000000000, 2),
                'sample_rate': _build_param('num', sample_rate, 'sps', 520834, 61440000, 2),
                'bandwidth': _build_param('num', bandwidth, 'Hz', 200000, 56000000, 1),
                'gain': _build_param('num', gain, 'dB', -15, 60, 1),
                'gain_mode': _build_param('select', gain_mode, options=[
                    'Default', 'Manual', 'FastAttack_AGC', 'SlowAttack_AGC', 'Hybrid_AGC'
                ]),
                'bias_tee': _build_param('bool', bias, options=[True, False]),
                'agc_enabled': _build_param('bool', agc_enabled, options=[True, False]),
            },
        }

    device_list: List[Dict[str, Any]] = []
    for i, name in enumerate(selected_devices):
        freq = frequency_e6 if i == 0 else frequency_e1
        bias = bias_tee_blade1 if i == 0 else bias_tee_blade2
        device_list.append(_device_entry(name, freq, bias))

    return {
        'device_list': device_list,
        'record_data': {
            'num_samples': num_samples,
            'duration': duration_seconds,
            'record_now': record_now,
            'record_at': record_at,
            'get_teslas': get_tesla_keys,
            'selected_devices': selected_devices,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# High-level Recording Functions
# ─────────────────────────────────────────────────────────────────────────────

def schedule_and_download(
    api: BladeUIAPI,
    config: Dict[str, Any],
    output_dir: Union[str, Path],
    poll_interval: float = 5.0,
    timeout_minutes: float = 30.0,
    logger: Optional[callable] = None,
) -> Dict[str, Any]:
    """
    Schedule a recording and wait for it to complete, then download files.
    
    Args:
        api: Authenticated BladeUIAPI instance
        config: Recording configuration from build_bladerf_config()
        output_dir: Directory to save downloaded files
        poll_interval: Seconds between status checks
        timeout_minutes: Maximum time to wait for recording
        logger: Optional logging function
        
    Returns:
        Dict with status and downloaded file paths
    """
    def log(msg: str):
        if logger:
            logger(msg)
        else:
            print(f"[BladeAPI] {msg}")
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    record_data = config.get('record_data', {})
    record_now = record_data.get('record_now', False)
    duration = record_data.get('duration', 1.0)
    
    # Schedule recording
    log("Requesting recording...")
    response = api.request_recording(config)
    log(f"Recording request sent: {response}")
    
    # Determine when to start polling
    if record_now:
        record_time = datetime.now(timezone.utc)
        ready_after = record_time + timedelta(seconds=duration + 5)
    else:
        record_at_str = record_data.get('record_at', '')
        if record_at_str:
            record_time = datetime.strptime(record_at_str, '%Y-%m-%dT%H:%M:%S')
            record_time = record_time.replace(tzinfo=timezone.utc)
        else:
            record_time = datetime.now(timezone.utc)
        ready_after = record_time + timedelta(seconds=duration + 5)
    
    # Wait until recording should be ready
    now = datetime.now(timezone.utc)
    if ready_after > now:
        wait_seconds = (ready_after - now).total_seconds()
        log(f"Waiting {wait_seconds:.0f}s for recording to complete...")
        time.sleep(wait_seconds)
    
    # Poll for completion
    timeout = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
    downloaded_files = []
    
    while datetime.now(timezone.utc) < timeout:
        status = api.check_recording_status(record_time)
        state = status.get('records_state', 'unknown')
        log(f"Recording state: {state}")
        
        if state == 'Completed':
            records = status.get('records', [])
            
            # Create folder structure from first record path
            if records:
                folder_name = Path(records[0].get('path', '')).parts[0]
                if folder_name:
                    (output_path / folder_name).mkdir(parents=True, exist_ok=True)
            
            for record in records:
                remote_path = record.get('path', '')
                device_name = record.get('device_name', 'unknown')
                local_path = output_path / remote_path
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                log(f"Downloading {device_name}: {remote_path}")
                api.download(f"/{remote_path}", local_path)
                downloaded_files.append({
                    'device': device_name,
                    'path': str(local_path),
                    'remote_path': remote_path,
                })
            
            return {
                'status': 'success',
                'downloaded_files': downloaded_files,
                'record_time': record_time.isoformat(),
            }
        
        elif state == 'Failed':
            return {
                'status': 'failed',
                'error': 'Recording failed on server',
                'details': status,
            }
        
        log(f"Recording not ready, waiting {poll_interval}s...")
        time.sleep(poll_interval)
    
    return {
        'status': 'timeout',
        'error': f'Recording did not complete within {timeout_minutes} minutes',
    }


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    'BladeUIAPI',
    'build_bladerf_config',
    'schedule_and_download',
]


# ─────────────────────────────────────────────────────────────────────────────
# Main (example usage)
# ─────────────────────────────────────────────────────────────────────────────