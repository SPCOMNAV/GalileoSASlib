"""
snapshot_naming.py
------------------
Helpers to parse the embedded metadata from snapshot filenames produced by the
BladeRF recording pipeline.

Two filename conventions are recognised:

  * Geographic position
        "<...>41N4996_2E1127<...>"   →  (41.4996, 2.1127)
        "<...>41N5007_2E1136<...>"   →  (41.5007, 2.1136)

  * UTC timestamp (always present in live recordings)
        "<...>20250604_110658Z<...>" →  datetime(2025-06-04 11:06:58 UTC)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple


# DOY HH:MM:SS for scheduled-time fields, e.g. "039 14:30:00"
_DOY_RE = re.compile(r'^(\d{1,3})\s+(\d{1,2}):(\d{2}):(\d{2})$')

# NN[NS]DDDD_NN[EW]DDDD coordinate token, e.g. 41N4996_2E1127
_LATLON_RE = re.compile(r'(\d+)([NS])(\d+)_(\d+)([EW])(\d+)')

# YYYYMMDD_HHMMSSZ UTC timestamp token, e.g. 20250604_110658Z
_TIMESTAMP_RE = re.compile(r'(\d{8})_(\d{6})Z')


def parse_scheduled_time_doy(scheduled_time_doy: str) -> Tuple[bool, str]:
    """Parse a scheduled_time_doy field in DOY HH:MM:SS format.

    Args:
        scheduled_time_doy: ``"0"`` for record-now, or ``"DDD HH:MM:SS"``
            (day-of-year + UTC time, e.g. ``"039 14:30:00"``).

    Returns:
        Tuple ``(record_now, scheduled_time_iso)``. ``scheduled_time_iso`` is
        empty when ``record_now`` is True or when the input is malformed.
    """
    if not scheduled_time_doy or scheduled_time_doy.strip() == "0":
        return True, ""

    match = _DOY_RE.match(scheduled_time_doy.strip())
    if not match:
        return True, ""

    try:
        doy = int(match.group(1))
        hour = int(match.group(2))
        minute = int(match.group(3))
        second = int(match.group(4))
        current_year = datetime.now(timezone.utc).year
        base_date = datetime(current_year, 1, 1, hour, minute, second, tzinfo=timezone.utc)
        scheduled_dt = base_date + timedelta(days=doy - 1)
        return False, scheduled_dt.isoformat()
    except (ValueError, AttributeError):
        return True, ""


def extract_location_from_snapshot_filename(
    filepath: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Extract latitude and longitude from a snapshot filename.

    Returns ``(None, None)`` if the filename does not contain the
    NN[NS]DDDD_NN[EW]DDDD token.
    """
    if not filepath:
        return None, None

    match = _LATLON_RE.search(Path(filepath).name)
    if not match:
        return None, None

    try:
        lat = int(match.group(1)) + float(f"0.{match.group(3)}")
        lon = int(match.group(4)) + float(f"0.{match.group(6)}")
        if match.group(2) == 'S':
            lat = -lat
        if match.group(5) == 'W':
            lon = -lon
        return lat, lon
    except (ValueError, IndexError, OverflowError):
        return None, None


def extract_datetime_from_snapshot_filename(filepath: str) -> Optional[datetime]:
    """Extract the UTC datetime token (YYYYMMDD_HHMMSSZ) from a snapshot
    filename, if present."""
    if not filepath:
        return None
    match = _TIMESTAMP_RE.search(Path(filepath).name)
    if not match:
        return None
    try:
        return datetime.strptime(
            match.group(1) + match.group(2), '%Y%m%d%H%M%S'
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


__all__ = [
    "parse_scheduled_time_doy",
    "extract_location_from_snapshot_filename",
    "extract_datetime_from_snapshot_filename",
]
