"""
Module_EphemeridesManagement.py
------------------------------
Block that downloads Galileo broadcast ephemerides (RINEX NAV) and computes
satellite ECEF positions needed for PVT.

Source: ESA GNSS Service Centre — Wing FTP WebClient at gssc.esa.int/webftp/
        (anonymous login, session-based AJAX: chdir.html → download)

The block parses RINEX 3.x navigation files for Galileo (E-prefix satellites)
and provides orbital elements + position computation via Kepler propagation.
"""

from __future__ import annotations

import os
import re
import gzip
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import math
from xml.etree import ElementTree

from utils.utils import (
    coerce_string,
    coerce_int,
    coerce_float,
    coerce_bool,
    load_schema as _load_schema,
    load_config as _load_config,
    save_config as _save_config,
    console_log,
    BASE_DIR,
    get_results_subdir,
)
from utils.time_galileo import GalileoTime
from utils.execution_context import get_execution_context

################################################ CONSTANTS ################################################

MODULE_FILE = "EphemeridesManagement"

# Galileo-specific constants
GALILEO_GM = 3.986004418e14  # m³/s² - Gravitational constant × Earth mass
GALILEO_OMEGA_E = 7.2921151467e-5  # rad/s - Earth rotation rate
SPEED_OF_LIGHT = 299792458.0  # m/s

# Galileo satellite list (PRN E01-E36)
GALILEO_SVIDS = list(range(1, 37))

# ── ESA Wing FTP WebClient endpoints ────────────────────────────────────────
ESA_WEBFTP_BASE = "https://gssc.esa.int/webftp"
ESA_WEBFTP_LOGIN = f"{ESA_WEBFTP_BASE}/loginok.html"
ESA_WEBFTP_CHDIR = f"{ESA_WEBFTP_BASE}/chdir.html"
ESA_WEBFTP_DOWNLOAD = f"{ESA_WEBFTP_BASE}"  # /?download&filename=...

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
    console_log("EphemeridesManagement", msg)




################################################# ESA WEBFTP CLIENT #################################################

class ESAWebFTPClient:
    """
    Lightweight client for the ESA GSSC Wing FTP Web interface.

    Protocol (reverse-engineered from gssc.esa.int/webftp/main.html):
      1. POST loginok.html  {username, password}  → session cookie UID
      2. POST chdir.html    {dir: <path>}         → XML with <rowdata> entries
      3. GET  /?download&filename=<path>           → file bytes
    """

    def __init__(self, timeout: int = 30):
        self._session = requests.Session()
        self._timeout = timeout
        self._logged_in = False

    # ── login ────────────────────────────────────────────────────────────
    def login(self, username: str = "anonymous", password: str = "anonymous") -> bool:
        """Login to ESA WebFTP. Returns True on success."""
        try:
            # Fetch login page first (may set initial cookies)
            self._session.get(f"{ESA_WEBFTP_BASE}/login.html", timeout=self._timeout)

            data = {"username": username, "password": password}
            r = self._session.post(ESA_WEBFTP_LOGIN, data=data, timeout=self._timeout)

            # On failure the response contains alert('Login failed: ...')
            if "Login failed" in r.text:
                import re as _re
                m = _re.search(r"alert\('([^']+)'\)", r.text)
                reason = m.group(1) if m else "unknown"
                _debug(f"ESA WebFTP login failed: {reason}")
                return False

            # Check for UID cookie or successful redirect (no alert)
            if self._session.cookies.get("UID") or "main.html" in r.text:
                self._logged_in = True
                _debug("ESA WebFTP login OK")
                return True

            _debug("ESA WebFTP login: no UID cookie and no redirect")
            return False
        except Exception as e:
            _debug(f"ESA WebFTP login error: {e}")
            return False

    # ── list directory ───────────────────────────────────────────────────
    def listdir(self, remote_dir: str) -> List[Dict[str, str]]:
        """
        List a remote directory.
        Returns list of dicts with keys: name, size, date, is_dir.
        """
        if not self._logged_in:
            return []
        try:
            # Step 1: chdir into the target directory
            self._session.post(
                ESA_WEBFTP_CHDIR,
                data={"dir": remote_dir},
                timeout=self._timeout,
            )

            # Step 2: retrieve the directory listing via dir.html
            ESA_WEBFTP_DIR = f"{ESA_WEBFTP_BASE}/dir.html"
            r = self._session.get(ESA_WEBFTP_DIR, timeout=self._timeout)
            if r.status_code != 200 or not r.text.strip():
                return []

            entries: List[Dict[str, str]] = []
            try:
                root = ElementTree.fromstring(r.text)
                for row in root.iter("rowdata"):
                    entry = {child.tag: (child.text or "") for child in row}
                    entries.append(entry)
            except ElementTree.ParseError:
                # Fallback: try regex on raw text
                for m in re.finditer(r'<rowdata>(.*?)</rowdata>', r.text, re.DOTALL):
                    block = m.group(1)
                    name_m = re.search(r'<name>([^<]+)</name>', block)
                    if name_m:
                        entries.append({"name": name_m.group(1)})
            return entries
        except Exception as e:
            _debug(f"ESA WebFTP listdir error: {e}")
            return []

    # ── download ─────────────────────────────────────────────────────────
    def download(self, remote_path: str) -> Optional[bytes]:
        """
        Download a file from WebFTP. Returns raw bytes or None.

        Wing FTP protocol requires:
          1. chdir to the file's parent directory
          2. GET /?download&filename=<basename>  (just the filename, not full path)
        """
        if not self._logged_in:
            return None
        try:
            # Split into directory + filename
            from pathlib import PurePosixPath
            pp = PurePosixPath(remote_path)
            parent_dir = str(pp.parent)
            filename = pp.name

            # chdir first
            self._session.post(
                ESA_WEBFTP_CHDIR,
                data={"dir": parent_dir},
                timeout=self._timeout,
            )

            # Download using just the filename
            url = f"{ESA_WEBFTP_DOWNLOAD}/?download&filename={filename}"
            r = self._session.get(url, timeout=120, stream=True)
            if r.status_code == 200 and len(r.content) > 100:
                return r.content
            return None
        except Exception as e:
            _debug(f"ESA WebFTP download error: {e}")
            return None

    def close(self):
        """Logout (best effort)."""
        try:
            self._session.get(f"{ESA_WEBFTP_BASE}/logout.html", timeout=5)
        except Exception:
            pass


################################################# DOWNLOAD: ESA GSSC #################################################

def download_broadcast_ephemerides_esa(
    target_datetime: datetime,
    output_dir: Path,
    prn_list: Optional[List[int]] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Download Galileo broadcast ephemerides from ESA GSSC Wing FTP.

    Tries two directory layouts on the WebFTP:
      1) /gnss/data/hourly/<YYYY>/<DDD>/<HH>/  (hourly RINEX – most recent data)
      2) /products/nav/<YYYY>/<DDD>/            (daily consolidated)

    Args:
        target_datetime: UTC datetime of the observation
        output_dir:      Where to save the file
        prn_list:        (unused – whole file is downloaded)
    Returns:
        (Path to decompressed RINEX, None)  on success
        (None, error_string)                on failure
    """
    year = target_datetime.year
    doy = GalileoTime.from_datetime(target_datetime).day_of_year
    _debug(f"Trying ESA GSSC WebFTP for {year}/{doy:03d} …")

    client = ESAWebFTPClient(timeout=30)
    if not client.login():
        return None, "ESA WebFTP login failed (too many sessions or server down)"

    try:
        entries = []
        nav_dir = ""

        # ── Strategy 1: /gnss/data/hourly/YYYY/DDD/HH  (most recent) ────
        for day_offset in range(3):
            dt_try = target_datetime - timedelta(days=day_offset)
            y = dt_try.year
            d = GalileoTime.from_datetime(dt_try).day_of_year
            base_hourly = f"/gnss/data/hourly/{y}/{d:03d}"

            # List the hour-folders available for this DOY
            hour_folders = client.listdir(base_hourly)
            if hour_folders:
                # Get available hour numbers, pick the most recent one ≤ target hour
                avail_hours = sorted(
                    [int(e["name"]) for e in hour_folders
                     if "name" in e and e["name"].isdigit()],
                    reverse=True,
                )
                target_hour = dt_try.hour if day_offset == 0 else 23
                # Pick best hour: first one ≤ target_hour, else the largest available
                best_hour = None
                for h in avail_hours:
                    if h <= target_hour:
                        best_hour = h
                        break
                if best_hour is None and avail_hours:
                    best_hour = avail_hours[0]

                if best_hour is not None:
                    nav_dir = f"{base_hourly}/{best_hour:02d}"
                    entries = client.listdir(nav_dir)
                    if entries:
                        if day_offset > 0:
                            _debug(f"No hourly RINEX for DOY {doy:03d}, using DOY {d:03d}/{best_hour:02d} (−{day_offset}d)")
                        else:
                            _debug(f"Found hourly RINEX at {nav_dir}")
                        break
                    entries = []

        # Check if hourly entries contain a BRDC (all-satellites) file
        hourly_entries = list(entries)   # keep a copy for fallback
        hourly_nav_dir = nav_dir
        has_brdc_hourly = any(
            e.get("name", "").startswith("BRDC") for e in hourly_entries
        )

        # ── Strategy 2: /products/nav/YYYY/DDD  (daily consolidated) ─────
        # Also try this when Strategy 1 only found station-specific files
        # (no BRDC), because station files cover only a subset of PRNs.
        if not entries or not has_brdc_hourly:
            reason = "Hourly path empty" if not hourly_entries else "No BRDC in hourly dir, checking daily"
            _debug(f"{reason}, trying /products/nav/ …")
            daily_entries: list = []
            daily_nav_dir = ""
            for day_offset in range(10):
                dt_try = target_datetime - timedelta(days=day_offset)
                y = dt_try.year
                d = GalileoTime.from_datetime(dt_try).day_of_year
                daily_nav_dir = f"/products/nav/{y}/{d:03d}"
                daily_entries = client.listdir(daily_nav_dir)

                if not daily_entries:
                    # Some years have hourly sub-directories under products/nav too
                    hour = dt_try.hour if day_offset == 0 else 23
                    for h in [hour, (hour - 1) % 24, (hour + 1) % 24]:
                        hourly_dir = f"{daily_nav_dir}/{h:02d}"
                        daily_entries = client.listdir(hourly_dir)
                        if daily_entries:
                            daily_nav_dir = hourly_dir
                            break

                if daily_entries:
                    if day_offset > 0:
                        _debug(f"No daily RINEX for DOY {doy:03d}, using DOY {d:03d} (−{day_offset}d)")
                    break

            # If daily has a BRDC file, prefer it over hourly station files
            has_brdc_daily = any(
                e.get("name", "").startswith("BRDC") for e in daily_entries
            )
            if has_brdc_daily:
                entries = daily_entries
                nav_dir = daily_nav_dir
                _debug(f"Using daily BRDC from {nav_dir}")
            elif daily_entries and not hourly_entries:
                # No hourly at all — use whatever daily has
                entries = daily_entries
                nav_dir = daily_nav_dir
            else:
                # Keep hourly entries (station files) as last resort
                entries = hourly_entries
                nav_dir = hourly_nav_dir

        if not entries:
            return None, f"ESA WebFTP: no files in /gnss/data/hourly/{year}/{doy:03d} or /products/nav/{year}/{doy:03d}"

        # Pick the best file: prefer BRDC (all satellites) over single-station files,
        # then largest *_MN.rnx.gz (Mixed Nav), then *_EN.rnx.gz
        # Build (brdc_priority, suffix_priority, -size, name) tuples for sorting.
        suffix_priority = {"_MN.rnx.gz": 0, "_EN.rnx.gz": 1, "_GN.rnx.gz": 2}
        scored: List[Tuple[int, int, int, str]] = []
        for e in entries:
            name = e.get("name", "")
            size = int(e.get("size", "0") or "0")
            # BRDC files contain all satellites; station files are partial
            is_brdc = 0 if name.startswith("BRDC") else 1
            for sfx, prio in suffix_priority.items():
                if name.endswith(sfx):
                    scored.append((is_brdc, prio, -size, name))
                    break

        if not scored:
            # Fall back: any .rnx.gz
            for e in entries:
                name = e.get("name", "")
                if name.endswith(".rnx.gz"):
                    size = int(e.get("size", "0") or "0")
                    is_brdc = 0 if name.startswith("BRDC") else 1
                    scored.append((is_brdc, 9, -size, name))

        if not scored:
            candidates = [e.get("name", "") for e in entries[:5]]
            return None, f"ESA WebFTP: no RINEX nav file in {nav_dir} (files: {candidates})"

        scored.sort()
        target_file = scored[0][3]
        _debug(f"Selected {target_file} (size={-scored[0][2]} B, {len(scored)} nav candidates)")

        # Download
        remote_path = f"{nav_dir}/{target_file}"
        _debug(f"Downloading {remote_path}")
        data = client.download(remote_path)
        if data is None:
            return None, f"ESA WebFTP: download failed for {remote_path}"

        output_dir.mkdir(parents=True, exist_ok=True)
        return _save_and_decompress(data, target_file, output_dir)

    finally:
        client.close()





################################################# DOWNLOAD: IGS BKG (fallback) ##########################################

def download_broadcast_ephemerides_igs(
    target_datetime: datetime,
    output_dir: Path,
    prn_list: Optional[List[int]] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Download broadcast ephemerides from IGS BKG (https://igs.bkg.bund.de).

    Directory layout:
      /root_ftp/IGS/BRDC/<YYYY>/<DDD>/  →  BRDC00IGS_R_<YYYY><DDD>0000_01D_MN.rnx.gz

    Tries target day and up to 3 previous days.
    """
    year = target_datetime.year
    doy  = GalileoTime.from_datetime(target_datetime).day_of_year
    _debug(f"Trying IGS BKG for {year}/{doy:03d} …")

    base_url = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC"

    for day_offset in range(4):
        dt_try = target_datetime - timedelta(days=day_offset)
        y = dt_try.year
        d = GalileoTime.from_datetime(dt_try).day_of_year

        # Standard IGS naming convention
        fname = f"BRDC00IGS_R_{y}{d:03d}0000_01D_MN.rnx.gz"
        url   = f"{base_url}/{y}/{d:03d}/{fname}"

        try:
            _debug(f"IGS BKG: trying {url}")
            resp = requests.get(url, timeout=30, verify=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                if day_offset > 0:
                    _debug(f"No RINEX for DOY {doy:03d} on IGS, using DOY {d:03d} (−{day_offset}d)")
                output_dir.mkdir(parents=True, exist_ok=True)
                return _save_and_decompress(resp.content, fname, output_dir)
            else:
                _debug(f"IGS BKG: {resp.status_code} for DOY {d:03d}")
        except Exception as e:
            _debug(f"IGS BKG error for DOY {d:03d}: {e}")

    return None, f"IGS BKG: no RINEX found for DOY {doy:03d}–{GalileoTime.from_datetime(target_datetime - timedelta(days=3)).day_of_year:03d}"


def _save_and_decompress(
    raw_bytes: bytes,
    filename: str,
    output_dir: Path,
) -> Tuple[Optional[Path], Optional[str]]:
    """Save (and gunzip if .gz) downloaded data. Returns (path, None) on success."""
    output_dir.mkdir(parents=True, exist_ok=True)
    compressed_path = output_dir / filename

    with open(compressed_path, "wb") as f:
        f.write(raw_bytes)

    if filename.endswith(".gz"):
        try:
            decompressed_path = output_dir / filename[:-3]
            with gzip.open(compressed_path, "rb") as f_in:
                with open(decompressed_path, "wb") as f_out:
                    f_out.write(f_in.read())
            os.remove(compressed_path)
            _debug(f"Saved & decompressed → {decompressed_path.name}")
            return decompressed_path, None
        except Exception as e:
            _debug(f"Decompression failed, keeping .gz: {e}")
            return compressed_path, None
    else:
        _debug(f"Saved → {compressed_path.name}")
        return compressed_path, None


################################################# EPHEMERIDES PARSING #################################################

def parse_rinex_nav_header(file_path: Path) -> Dict[str, Any]:
    """
    Parse RINEX navigation file header.
    
    Args:
        file_path: Path to RINEX navigation file
        
    Returns:
        Dictionary with header information
    """
    header = {}
    
    try:
        with open(file_path, 'r', errors='ignore') as f:
            for line in f:
                if 'END OF HEADER' in line:
                    break
                
                # Parse header fields
                if 'RINEX VERSION' in line:
                    header['version'] = line[:9].strip()
                    header['type'] = line[20:21].strip()
                elif 'PGM / RUN BY' in line:
                    header['program'] = line[:20].strip()
                elif 'IONOSPHERIC CORR' in line:
                    corr_type = line[:4].strip()
                    if 'ionospheric_corr' not in header:
                        header['ionospheric_corr'] = {}
                    header['ionospheric_corr'][corr_type] = line[5:60].strip()
                elif 'LEAP SECONDS' in line:
                    header['leap_seconds'] = int(line[:6].strip())
        
        _debug(f"Parsed RINEX header: version={header.get('version')}, type={header.get('type')}")
        return header
        
    except Exception as e:
        _debug(f"Error parsing RINEX header: {e}")
        return {}


def parse_rinex_nav_galileo(file_path: Path) -> List[Dict[str, Any]]:
    """
    Parse Galileo ephemerides from RINEX navigation file.
    
    Extracts orbital parameters for each Galileo satellite including:
    - Clock corrections (af0, af1, af2)
    - Orbital elements (a, e, i, omega, Omega, M0)
    - Perturbation parameters (Crs, Crc, Cus, Cuc, Cis, Cic)
    - Time parameters (toe, toc)
    
    Args:
        file_path: Path to RINEX NAV file
        
    Returns:
        List of ephemeris dictionaries for Galileo satellites
    """
    ephemerides = []
    
    try:
        with open(file_path, 'r', errors='ignore') as f:
            content = f.read()
        
        # Skip header
        if 'END OF HEADER' in content:
            content = content.split('END OF HEADER')[1]
        
        lines = content.strip().split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # Check for Galileo satellite (E## prefix in RINEX 3.x, or PRN 1-36 check)
            # RINEX 3.x: "E01 2024 01 15 ..."
            if len(line) > 3 and line[0] == 'E':
                try:
                    # Parse satellite and epoch
                    prn = int(line[1:3])
                    year = int(line[4:8])
                    month = int(line[9:11])
                    day = int(line[12:14])
                    hour = int(line[15:17])
                    minute = int(line[18:20])
                    second = float(line[21:23])
                    
                    # Clock parameters (line 1)
                    af0 = float(line[23:42].replace('D', 'E'))
                    af1 = float(line[42:61].replace('D', 'E'))
                    af2 = float(line[61:80].replace('D', 'E'))
                    
                    # Need 7 more lines for complete ephemeris
                    if i + 7 >= len(lines):
                        break
                    
                    eph = {
                        'prn': prn,
                        'svid': f'E{prn:02d}',
                        'toc': datetime(year, month, day, hour, minute, int(second), tzinfo=timezone.utc),
                        'af0': af0,  # Clock bias (s)
                        'af1': af1,  # Clock drift (s/s)
                        'af2': af2,  # Clock drift rate (s/s²)
                    }
                    
                    # Parse remaining 7 lines of broadcast orbit
                    # Line 2: IODE, Crs, Delta_n, M0
                    l2 = lines[i+1]
                    eph['iode'] = float(l2[4:23].replace('D', 'E'))
                    eph['crs'] = float(l2[23:42].replace('D', 'E'))
                    eph['delta_n'] = float(l2[42:61].replace('D', 'E'))
                    eph['m0'] = float(l2[61:80].replace('D', 'E'))
                    
                    # Line 3: Cuc, e, Cus, sqrt_a
                    l3 = lines[i+2]
                    eph['cuc'] = float(l3[4:23].replace('D', 'E'))
                    eph['e'] = float(l3[23:42].replace('D', 'E'))
                    eph['cus'] = float(l3[42:61].replace('D', 'E'))
                    eph['sqrt_a'] = float(l3[61:80].replace('D', 'E'))
                    
                    # Line 4: toe, Cic, OMEGA0, Cis
                    l4 = lines[i+3]
                    eph['toe'] = float(l4[4:23].replace('D', 'E'))
                    eph['cic'] = float(l4[23:42].replace('D', 'E'))
                    eph['omega0'] = float(l4[42:61].replace('D', 'E'))
                    eph['cis'] = float(l4[61:80].replace('D', 'E'))
                    
                    # Line 5: i0, Crc, omega, OMEGA_DOT
                    l5 = lines[i+4]
                    eph['i0'] = float(l5[4:23].replace('D', 'E'))
                    eph['crc'] = float(l5[23:42].replace('D', 'E'))
                    eph['omega'] = float(l5[42:61].replace('D', 'E'))
                    eph['omega_dot'] = float(l5[61:80].replace('D', 'E'))
                    
                    # Line 6: IDOT, Data sources, GAL Week, Spare
                    l6 = lines[i+5]
                    eph['idot'] = float(l6[4:23].replace('D', 'E'))
                    eph['data_sources'] = int(float(l6[23:42].replace('D', 'E')))
                    eph['gal_week'] = int(float(l6[42:61].replace('D', 'E')))
                    
                    # Line 7: SISA, SVhealth, BGD E5a/E1, BGD E5b/E1
                    l7 = lines[i+6]
                    eph['sisa'] = float(l7[4:23].replace('D', 'E'))
                    eph['sv_health'] = int(float(l7[23:42].replace('D', 'E')))
                    eph['bgd_e5a_e1'] = float(l7[42:61].replace('D', 'E'))
                    eph['bgd_e5b_e1'] = float(l7[61:80].replace('D', 'E'))
                    
                    # Line 8: Transmission time
                    l8 = lines[i+7]
                    eph['transmission_time'] = float(l8[4:23].replace('D', 'E'))
                    
                    ephemerides.append(eph)
                    i += 8
                    continue
                    
                except (ValueError, IndexError) as e:
                    _debug(f"Error parsing Galileo ephemeris at line {i}: {e}")
            
            i += 1
        
        _debug(f"Parsed {len(ephemerides)} Galileo ephemeris records")
        return ephemerides
        
    except Exception as e:
        _debug(f"Error parsing RINEX NAV file: {e}")
        return []


def calculate_satellite_position(eph: Dict[str, Any], t: float) -> Dict[str, float]:
    """
    Calculate satellite position from broadcast ephemeris.
    
    Implements the standard GNSS orbit computation algorithm.
    
    Args:
        eph: Ephemeris dictionary with orbital parameters
        t: GPS/Galileo time (seconds of week)
        
    Returns:
        Dictionary with x, y, z position in ECEF (meters)
    """
    # Extract orbital parameters
    sqrt_a = eph['sqrt_a']
    e = eph['e']
    m0 = eph['m0']
    omega = eph['omega']
    omega0 = eph['omega0']
    i0 = eph['i0']
    delta_n = eph['delta_n']
    omega_dot = eph['omega_dot']
    idot = eph['idot']
    cuc = eph['cuc']
    cus = eph['cus']
    crc = eph['crc']
    crs = eph['crs']
    cic = eph['cic']
    cis = eph['cis']
    toe = eph['toe']
    
    # Semi-major axis
    a = sqrt_a ** 2
    
    # Computed mean motion
    n0 = math.sqrt(GALILEO_GM / (a ** 3))
    
    # Corrected mean motion
    n = n0 + delta_n
    
    # Time from ephemeris reference epoch
    tk = t - toe
    if tk > 302400:
        tk -= 604800
    elif tk < -302400:
        tk += 604800
    
    # Mean anomaly
    mk = m0 + n * tk
    
    # Solve Kepler's equation for eccentric anomaly (iterative)
    ek = mk
    for _ in range(10):
        ek_new = mk + e * math.sin(ek)
        if abs(ek_new - ek) < 1e-12:
            break
        ek = ek_new
    
    # True anomaly
    sin_vk = (math.sqrt(1 - e**2) * math.sin(ek)) / (1 - e * math.cos(ek))
    cos_vk = (math.cos(ek) - e) / (1 - e * math.cos(ek))
    vk = math.atan2(sin_vk, cos_vk)
    
    # Argument of latitude
    phi_k = vk + omega
    
    # Second harmonic perturbations
    delta_uk = cus * math.sin(2 * phi_k) + cuc * math.cos(2 * phi_k)
    delta_rk = crs * math.sin(2 * phi_k) + crc * math.cos(2 * phi_k)
    delta_ik = cis * math.sin(2 * phi_k) + cic * math.cos(2 * phi_k)
    
    # Corrected argument of latitude, radius, and inclination
    uk = phi_k + delta_uk
    rk = a * (1 - e * math.cos(ek)) + delta_rk
    ik = i0 + idot * tk + delta_ik
    
    # Positions in orbital plane
    xk_prime = rk * math.cos(uk)
    yk_prime = rk * math.sin(uk)
    
    # Corrected longitude of ascending node
    omega_k = omega0 + (omega_dot - GALILEO_OMEGA_E) * tk - GALILEO_OMEGA_E * toe
    
    # ECEF coordinates
    x = xk_prime * math.cos(omega_k) - yk_prime * math.cos(ik) * math.sin(omega_k)
    y = xk_prime * math.sin(omega_k) + yk_prime * math.cos(ik) * math.cos(omega_k)
    z = yk_prime * math.sin(ik)
    
    return {'x': x, 'y': y, 'z': z}


################################################# MAIN PIPELINE #################################################


def _select_best_ephemeris(
    ephemerides: List[Dict[str, Any]],
    prn: int,
    target_dt: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Pick the broadcast ephemeris set closest to *target_dt* for a given PRN.
    Prefers healthy (sv_health == 0) records, but falls back to any record.
    """
    prn_ephs = [e for e in ephemerides if e["prn"] == prn]
    if not prn_ephs:
        return None

    healthy = [e for e in prn_ephs if e.get("sv_health", 0) == 0]
    pool = healthy if healthy else prn_ephs

    return min(pool, key=lambda e: abs((e["toc"] - target_dt).total_seconds()))


def _run_pipeline_legacy(
    callback_status=None,
    callback_result=None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute the Ephemerides Management pipeline.

    Downloads Galileo broadcast ephemerides (RINEX NAV), parses them,
    and computes satellite ECEF positions for the target PRNs.

    Accepted **kwargs (injected by the pipeline via get_block_kwargs):
        target_datetime_iso : str   – ISO-8601 UTC from snapshot filename
        prn_list_str        : str   – comma-separated PRN list (e.g. "14,15")
        snapshot_path       : str   – snapshot path (fallback for datetime)

    Returns dict with keys: success, ephemerides, satellite_positions, …
    """
    def report_status(msg: str, progress: float = 0.0):
        _debug(msg)
        if callable(callback_status):
            callback_status(msg, progress)

    def report_result(key: str, value: Any):
        if callable(callback_result):
            callback_result(key, value)

    # -- Load configuration -----------------------------------------------
    config = load_config()

    eph_type = coerce_string(config.get("ephemerides_type", "broadcast"))
    use_current_time = coerce_bool(config.get("use_current_time", True))
    target_datetime_str = coerce_string(config.get("target_datetime", ""))
    save_ephemerides = coerce_bool(config.get("save_ephemerides_file", True))
    prn_filter_cfg = coerce_string(config.get("prn_filter", ""))

    # -- Determine target datetime ----------------------------------------
    target_dt: Optional[datetime] = None

    # 1. kwargs from pipeline (highest priority – derived from snapshot filename)
    iso_kw = kwargs.get("target_datetime_iso")
    if iso_kw:
        try:
            target_dt = datetime.fromisoformat(iso_kw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    # 2. Fallback: extract from snapshot filename in kwargs
    if target_dt is None:
        snap_path = kwargs.get("snapshot_path", "")
        if snap_path:
            t = GalileoTime.from_snapshot_filename(snap_path)
            target_dt = t.to_datetime if t is not None else None

    # 3. Fallback: user-configured datetime
    if target_dt is None and not use_current_time and target_datetime_str:
        try:
            target_dt = datetime.fromisoformat(target_datetime_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    # 4. Last resort: current time
    if target_dt is None:
        target_dt = datetime.now(timezone.utc)

    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)

    report_status(f"Target datetime: {target_dt.isoformat()}", 0.05)
    report_result("target_datetime", target_dt.isoformat())

    gps_week, dow, sow = GalileoTime.from_datetime(target_dt).gps_week_dow
    report_result("gps_week", gps_week)
    report_result("day_of_week", dow)

    # -- Parse PRN filter --------------------------------------------------
    prn_str = kwargs.get("prn_list_str", "") or prn_filter_cfg
    prn_list: Optional[List[int]] = None
    if prn_str:
        try:
            prn_list = [int(p.strip().lstrip("Ee")) for p in prn_str.split(",") if p.strip()]
        except ValueError:
            prn_list = None

    # -- Output directory --------------------------------------------------
    # Prefer execution-specific downloads dir; fall back to global results dir
    ctx = get_execution_context()
    if ctx.get_execution_id():
        eph_dir = ctx.get_execution_downloads_dir("ephemerides")
    else:
        eph_dir = get_results_subdir("ephemerides")
    eph_dir.mkdir(parents=True, exist_ok=True)

    # =====================================================================
    # BROADCAST EPHEMERIDES
    # =====================================================================
    eph_path: Optional[Path] = None
    error_msg: Optional[str] = None
    ephemerides_data: List[Dict[str, Any]] = []

    if eph_type == "broadcast":
        report_status("Downloading broadcast ephemerides from ESA GSSC …", 0.1)

        try:
            eph_path, error_msg = download_broadcast_ephemerides_esa(
                target_dt, eph_dir, prn_list,
            )
        except Exception as e:
            error_msg = str(e)
            eph_path = None

        # -- Fallback to IGS BKG if ESA failed ----------------------------
        if not eph_path or not eph_path.exists():
            report_status(f"ESA failed ({error_msg}), trying IGS BKG …", 0.2)
            try:
                eph_path, error_msg_igs = download_broadcast_ephemerides_igs(
                    target_dt, eph_dir, prn_list,
                )
                if eph_path and eph_path.exists():
                    error_msg = None  # Clear the ESA error
                else:
                    error_msg = f"ESA: {error_msg}; IGS: {error_msg_igs}"
            except Exception as e:
                error_msg = f"ESA: {error_msg}; IGS: {e}"
                eph_path = None

        if eph_path and eph_path.exists():
            report_status(f"Downloaded: {eph_path.name}", 0.4)
        else:
            report_status(f"Download failed: {error_msg}", 0.4)

        # -- Parse RINEX ---------------------------------------------------
        if eph_path and eph_path.exists():
            report_status("Parsing RINEX broadcast ephemerides …", 0.6)
            report_result("ephemerides_file", str(eph_path))
            report_result("download_status", "success")

            header = parse_rinex_nav_header(eph_path)
            report_result("rinex_version", header.get("version", "unknown"))

            ephemerides_data = parse_rinex_nav_galileo(eph_path)

            if prn_list:
                ephemerides_data = [e for e in ephemerides_data if e["prn"] in prn_list]

    # =====================================================================
    # SUMMARY / SATELLITE POSITIONS
    # =====================================================================
    if not eph_path or not eph_path.exists():
        report_result("download_status", "failed")
        report_result("error_message", error_msg or "Unknown error")
        report_status(f"Failed to download ephemerides: {error_msg}", 0.8)

    unique_prns = set()
    healthy_count = 0
    unhealthy_count = 0
    for eph in ephemerides_data:
        prn = eph.get("prn")
        if prn:
            unique_prns.add(prn)
            if eph.get("sv_health", 0) == 0:
                healthy_count += 1
            else:
                unhealthy_count += 1

    report_result("ephemerides_count", len(ephemerides_data))
    report_result("unique_satellites", len(unique_prns))
    report_result("healthy_records", healthy_count)
    report_result("unhealthy_records", unhealthy_count)

    # -- Compute satellite positions for target PRNs -----------------------
    satellite_positions: Dict[int, Dict[str, float]] = {}
    if ephemerides_data:
        # Galileo time: seconds of week (same epoch as GPS for broadcast)
        gal_epoch = datetime(1999, 8, 22, tzinfo=timezone.utc)
        delta_gal = (target_dt - gal_epoch).total_seconds()
        gal_sow = delta_gal % 604800.0

        target_prns = prn_list if prn_list else sorted(unique_prns)
        for prn in target_prns:
            best = _select_best_ephemeris(ephemerides_data, prn, target_dt)
            if best is None:
                continue
            try:
                pos = calculate_satellite_position(best, gal_sow)
                satellite_positions[prn] = pos
                r = math.sqrt(pos["x"] ** 2 + pos["y"] ** 2 + pos["z"] ** 2)
                _debug(
                    f"E{prn:02d} ECEF = ({pos['x']/1e3:.1f}, {pos['y']/1e3:.1f}, {pos['z']/1e3:.1f}) km  "
                    f"r={r/1e3:.1f} km"
                )
            except Exception as e:
                _debug(f"Position computation failed for E{prn:02d}: {e}")

    report_result("satellite_positions_count", len(satellite_positions))
    report_status(
        f"Computed {len(satellite_positions)} satellite positions "
        f"({len(unique_prns)} unique PRNs in file)",
        0.85,
    )

    # -- Save parsed data as JSON ------------------------------------------
    if ephemerides_data and save_ephemerides:
        serializable_data = []
        for eph in ephemerides_data:
            eph_copy = dict(eph)
            if "toc" in eph_copy and isinstance(eph_copy["toc"], datetime):
                eph_copy["toc"] = eph_copy["toc"].isoformat()
            serializable_data.append(eph_copy)

        json_path = eph_dir / f"galileo_ephemerides_{target_dt.strftime('%Y%m%d')}.json"
        with open(json_path, "w") as f:
            json.dump(
                {
                    "target_datetime": target_dt.isoformat(),
                    "gps_week": gps_week,
                    "ephemerides_type": eph_type,
                    "source": "esa_gssc",
                    "satellite_positions": {
                        f"E{prn:02d}": pos for prn, pos in satellite_positions.items()
                    },
                    "ephemerides": serializable_data,
                },
                f,
                indent=2,
            )
        report_result("json_output", str(json_path))

    report_status("Ephemerides management complete", 1.0)

    return {
        "success": eph_path is not None,
        "target_datetime": target_dt.isoformat(),
        "gps_week": gps_week,
        "ephemerides_file": str(eph_path) if eph_path else None,
        "ephemerides_type": eph_type,
        "ephemerides_count": len(ephemerides_data),
        "unique_satellites": len(unique_prns),
        "satellite_positions": satellite_positions,
        "ephemerides": ephemerides_data,
        "error": error_msg if eph_path is None else None,
    }


############################################ PIPELINE EXECUTION (CONTRACT 1.0) ############################################

from utils.snapshot_naming import extract_datetime_from_snapshot_filename as _snap_dt


def run_pipeline(
    config=None,
    inputs=None,
    globals=None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: snapshot_path_e6, snapshot_path_e1
              (rf_input_*, svid_to_search come from globals)
    produces: ephemerides_data
    """
    inputs = dict(inputs or {})
    g = dict(globals or {})

    # Pick the freshest snapshot path: in live mode prefer ctx-published one,
    # in file mode prefer the user-configured path.
    is_live = not bool(g.get("rf_input_from_file", True))
    if is_live:
        rf_path = inputs.get("snapshot_path_e6") or inputs.get("snapshot_path_e1") or ""
    else:
        rf_path = (g.get("rf_input_path_e6") or g.get("rf_input_path_e1") or "")
    if not rf_path:
        rf_path = inputs.get("snapshot_path_e6") or inputs.get("snapshot_path_e1") or ""

    target_iso = ""
    if rf_path:
        snap_dt = _snap_dt(rf_path)
        if snap_dt:
            target_iso = snap_dt.isoformat()

    raw = _run_pipeline_legacy(
        target_datetime_iso=target_iso,
        snapshot_path=rf_path,
        prn_list_str=g.get("svid_to_search") or "",
    )
    return {
        "outputs": {
            "ephemerides_data": raw,
        },
        "summary": {
            "success": raw.get("success") if isinstance(raw, dict) else False,
            "ephemerides_count": raw.get("ephemerides_count") if isinstance(raw, dict) else 0,
            "unique_satellites": raw.get("unique_satellites") if isinstance(raw, dict) else 0,
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"
