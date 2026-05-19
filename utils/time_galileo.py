"""
utils/time_galileo.py
---------------------
GalileoTime — immutable value object for Galileo System Time (GST).

Internal state: one float — **true GST seconds** since the GST epoch.
True GST is a continuous SI-second counter identical to GPS time offset
by a fixed epoch difference.  All other representations (UTC datetime,
WN/TOW, GPS seconds, RECS timestamp, …) are derived properties.

Epoch reference:
    GST epoch : GPS week 1024 rollover.
                Calendar approximate: 1999-08-22 00:00:00 UTC.
                Python uses ``GST_EPOCH`` for arithmetic; the 18-second
                leap-second offset ``GPS_UTC_LEAP_S`` is applied in the
                UTC↔GST conversion methods to maintain true GST internally.
    GPS epoch : 1980-01-06 00:00:00 UTC
    Leap-second offset (GPS_UTC_LEAP_S = 18) is constant since 2017-01-01.
    Update GPS_UTC_LEAP_S if a new leap second is announced.

IMPORTANT – UTC vs GST calendar
    Timestamps in Galileo/RECS/OSNMA are expressed in the *GST calendar*
    (continuous, no leap seconds).  UTC datetimes include leap-second
    discontinuities.  When converting:
      • ``from_datetime(utc_dt)``     – input is UTC  → adds leap correction
      • ``from_gst_calendar(gst_dt)`` – input is a GST-calendar datetime
                                        → NO leap correction
      • ``to_datetime``               – output is UTC → subtracts leap correction
      • ``gst_calendar_datetime``     – output is GST calendar → no correction

Module-level convenience functions are kept for callers that prefer a
functional style instead of instantiating the class.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Galileo System Time epoch
GST_EPOCH = datetime(1999, 8, 22, 0, 0, 0, tzinfo=timezone.utc)

# GPS epoch
GPS_EPOCH = datetime(1980, 1, 6, 0, 0, 0, tzinfo=timezone.utc)

GST_WEEK_SECONDS: int = 604800      # seconds in one week
GST_WN_MODULO: int = 4096           # 12-bit week-number rollover used in RECS/OSNMA

# Leap-second offset: GPS/GAL system time is continuous (no leap seconds),
# while UTC inserts leap seconds. As of 2017-01-01 the difference is 18 s.
GPS_UTC_LEAP_S: int = 18

# Offset from GPS epoch to GST epoch in seconds (constant, ~631,152,000 s)
_GPS_GST_OFFSET: float = (GST_EPOCH - GPS_EPOCH).total_seconds()


# ─────────────────────────────────────────────────────────────────────────────
# GalileoTime
# ─────────────────────────────────────────────────────────────────────────────

class GalileoTime:
    """
    Immutable point in Galileo System Time.

    Construct via class methods (``from_*``), not directly — although the
    raw constructor ``GalileoTime(gst_seconds)`` is also valid.

    Examples::

        t = GalileoTime.from_datetime(datetime(2025, 6, 4, 11, 6, 58,
                                               tzinfo=timezone.utc))
        print(t.week_tow)           # (WN, TOW)  — unmodulated
        print(t.week_tow_12bit)     # (WN%4096, TOW) — for RECS/OSNMA
        print(t.recs_timestamp)     # "YYDDDHHMMSSS"
        print(t.gps_seconds_leap_corrected)  # for PVT solver

        t2 = t + 30          # 30 seconds later
        elapsed = t2 - t     # float seconds
    """

    __slots__ = ("_gst_sec",)

    # ── Construction ─────────────────────────────────────────────────────────

    def __init__(self, gst_seconds: float) -> None:
        """Construct from raw GST seconds since the GST epoch."""
        object.__setattr__(self, "_gst_sec", float(gst_seconds))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("GalileoTime is immutable")

    @classmethod
    def from_datetime(cls, dt: datetime) -> "GalileoTime":
        """
        Construct from a **UTC** datetime.

        Applies the leap-second correction so that the internal state is
        true GST seconds, consistent with ``from_week_tow``.
        Naive datetimes are assumed to be UTC.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return cls((dt - GST_EPOCH).total_seconds() + GPS_UTC_LEAP_S)

    @classmethod
    def from_gst_calendar(cls, dt: datetime) -> "GalileoTime":
        """
        Construct from a GST calendar datetime (e.g. parsed from a RECS
        filename or OSNMA timestamp).

        These timestamps are already in the GST time-scale, so NO
        leap-second correction is applied.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return cls((dt - GST_EPOCH).total_seconds())

    @classmethod
    def from_week_tow(cls, wn: int, tow: float) -> "GalileoTime":
        """Construct from (Week Number, Time of Week) in GST."""
        return cls(wn * GST_WEEK_SECONDS + tow)

    @classmethod
    def from_gps_seconds(cls, gps_sec: float) -> "GalileoTime":
        """
        Construct from continuous GPS seconds (measured from 1980-01-06).

        This does NOT apply the leap-second offset — GPS and GAL system
        clocks tick at the same rate, offset only by the epoch difference.
        """
        return cls(gps_sec - _GPS_GST_OFFSET)

    @classmethod
    def from_snapshot_filename(cls, filepath: str) -> Optional["GalileoTime"]:
        """
        Parse a snapshot filename and construct.

        Expected pattern inside the filename: ``YYYYMMDD_HHMMSSZ``
        (e.g., ``snapshot_a2_20250604_110658Z_41N4996_2E1127.bin``).

        Returns None if no recognisable timestamp is found.
        """
        if not filepath:
            return None
        fname = Path(filepath).name
        m = re.search(r"(\d{8})_(\d{6})Z", fname)
        if m:
            try:
                dt = datetime.strptime(
                    m.group(1) + m.group(2), "%Y%m%d%H%M%S"
                ).replace(tzinfo=timezone.utc)
                return cls.from_datetime(dt)
            except ValueError:
                pass
        return None

    @classmethod
    def now(cls) -> "GalileoTime":
        """Construct from the current UTC wall clock."""
        return cls.from_datetime(datetime.now(timezone.utc))

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def gst_seconds(self) -> float:
        """Total GST seconds since the GST epoch (internal state)."""
        return self._gst_sec

    @property
    def to_datetime(self) -> datetime:
        """UTC datetime representation (leap-second corrected)."""
        return GST_EPOCH + timedelta(seconds=self._gst_sec - GPS_UTC_LEAP_S)

    @property
    def gst_calendar_datetime(self) -> datetime:
        """
        GST calendar datetime (no leap-second correction).

        Use this for formatting GST timestamps (RECS filenames, OSNMA
        epochs).  NOT a valid UTC instant.
        """
        return GST_EPOCH + timedelta(seconds=self._gst_sec)

    @property
    def week_tow(self) -> Tuple[int, int]:
        """(Week Number, Time of Week) — unmodulated WN."""
        wn = int(self._gst_sec // GST_WEEK_SECONDS)
        tow = int(self._gst_sec % GST_WEEK_SECONDS)
        return wn, tow

    @property
    def week_tow_12bit(self) -> Tuple[int, int]:
        """
        (WN % 4096, TOW) — 12-bit modulated WN as used in RECS/OSNMA.

        Replaces the ``secondsToWnTow()`` wrapper in RECSDecryption.
        """
        wn, tow = self.week_tow
        return wn % GST_WN_MODULO, tow

    @property
    def gps_seconds(self) -> float:
        """
        Continuous GPS seconds from the GPS epoch (1980-01-06), without
        the leap-second correction.

        This is equivalent to ``(dt - GPS_EPOCH).total_seconds()``.
        """
        return self._gst_sec + _GPS_GST_OFFSET

    @property
    def gps_seconds_leap_corrected(self) -> float:
        """
        True GPS seconds (continuous, from 1980-01-06).

        Now that ``_gst_sec`` stores true GST, ``gps_seconds`` already
        returns true GPS seconds.  This property is kept for backward
        compatibility and returns the same value.

        Replaces ``_datetime_to_gal_sec()`` in PVTComputation.
        """
        return self._gst_sec + _GPS_GST_OFFSET

    @property
    def gps_tow(self) -> float:
        """
        GPS time-of-week in seconds (leap-corrected).

        Replaces ``_gal_sec_to_tow()`` in PVTComputation.
        """
        return self.gps_seconds_leap_corrected % GST_WEEK_SECONDS

    @property
    def gps_week_dow(self) -> Tuple[int, int, int]:
        """
        (GPS week, day of week, second of week) from the GPS epoch.

        Replaces ``gps_week_and_dow()`` in EphemeridesManagement.
        """
        gps_sec = self.gps_seconds  # no leap correction needed for calendar week
        total_days = int(gps_sec // 86400)
        gps_week = total_days // 7
        dow = total_days % 7
        sow = dow * 86400 + int(gps_sec % 86400)
        return gps_week, dow, sow

    @property
    def day_of_year(self) -> int:
        """
        Day of year (1–366) from the UTC representation.

        Replaces ``doy_from_datetime()`` in EphemeridesManagement.
        """
        return self.to_datetime.timetuple().tm_yday

    @property
    def recs_timestamp(self) -> str:
        """
        RECS tstart format: ``YYDDDHHMMSSS`` (12 characters).

        Uses the **GST calendar** (not UTC) because RECS filenames and
        the ACAS specification express timestamps in GST.

        - YY  : 2-digit year
        - DDD : day of year (001–366)
        - HH  : hour (00–23)
        - MM  : minute (00–59)
        - SSS : deciseconds (second × 10, 000–590)
        """
        dt = self.gst_calendar_datetime
        yy = dt.year % 100
        ddd = dt.timetuple().tm_yday
        hh = dt.hour
        mm = dt.minute
        sss = dt.second * 10
        return f"{yy:02d}{ddd:03d}{hh:02d}{mm:02d}{sss:03d}"

    # ── Arithmetic ────────────────────────────────────────────────────────────

    def __add__(self, seconds: float) -> "GalileoTime":
        """Return a new GalileoTime shifted forward by *seconds*."""
        return GalileoTime(self._gst_sec + float(seconds))

    def __sub__(self, other: object) -> "float | GalileoTime":
        """
        Subtract another GalileoTime → elapsed seconds (float).
        Subtract a number → shifted GalileoTime (negative shift).
        """
        if isinstance(other, GalileoTime):
            return self._gst_sec - other._gst_sec
        return GalileoTime(self._gst_sec - float(other))  # type: ignore[arg-type]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GalileoTime):
            return self._gst_sec == other._gst_sec
        return NotImplemented

    def __lt__(self, other: "GalileoTime") -> bool:
        return self._gst_sec < other._gst_sec

    def __le__(self, other: "GalileoTime") -> bool:
        return self._gst_sec <= other._gst_sec

    def __repr__(self) -> str:
        wn, tow = self.week_tow
        return (
            f"GalileoTime(WN={wn}, TOW={tow}, "
            f"UTC={self.to_datetime.isoformat()}, "
            f"GST_cal={self.gst_calendar_datetime.strftime('%H:%M:%S')})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level convenience functions
# (thin wrappers — keep existing call-sites working without instantiation)
# ─────────────────────────────────────────────────────────────────────────────

def gst_seconds_to_datetime(gst_sec: float) -> datetime:
    """Convert GST seconds to UTC datetime."""
    return GalileoTime(gst_sec).to_datetime


def datetime_to_gst_seconds(dt: datetime) -> float:
    """Convert UTC datetime to true GST seconds (leap-second corrected)."""
    return GalileoTime.from_datetime(dt).gst_seconds


def gst_calendar_to_gst_seconds(dt: datetime) -> float:
    """Convert GST calendar datetime to true GST seconds (no leap correction)."""
    return GalileoTime.from_gst_calendar(dt).gst_seconds


def gst_to_week_tow(gst_sec: float) -> Tuple[int, int]:
    """Convert GST seconds to (Week Number, Time of Week)."""
    return GalileoTime(gst_sec).week_tow


def week_tow_to_gst_seconds(wn: int, tow: float) -> float:
    """Convert (Week Number, Time of Week) to GST seconds."""
    return GalileoTime.from_week_tow(wn, tow).gst_seconds


def gst_to_recs_timestamp(gst_sec: float) -> str:
    """Convert GST seconds to RECS tstart string (YYDDDHHMMSSS)."""
    return GalileoTime(gst_sec).recs_timestamp


def generate_recs_filename(
    gst_sec: float,
    svid: int,
    kdi: int,
    provider: str = "GSCX",
    interface_version: str = "01",
    rand: int = 0,
    file_version: int = 1,
) -> str:
    """
    Generate the expected RECS filename for the given parameters.

    Format: ``XXXXII_YYDDDHHMMSSS_ss_K_R_VV.RCS``
    """
    timestamp = gst_to_recs_timestamp(gst_sec)
    return (
        f"{provider}{interface_version}_{timestamp}"
        f"_{svid:02d}_{kdi}_{rand}_{file_version:02d}.RCS"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Constants
    "GST_EPOCH",
    "GPS_EPOCH",
    "GST_WEEK_SECONDS",
    "GST_WN_MODULO",
    "GPS_UTC_LEAP_S",
    # Class
    "GalileoTime",
    # Convenience functions
    "gst_seconds_to_datetime",
    "datetime_to_gst_seconds",
    "gst_calendar_to_gst_seconds",
    "gst_to_week_tow",
    "week_tow_to_gst_seconds",
    "gst_to_recs_timestamp",
    "generate_recs_filename",
]
