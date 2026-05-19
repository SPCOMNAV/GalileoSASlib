"""
Module_AlmanacManagement.py
--------------------------
Block that downloads Galileo almanac data from ESA/GSC sources.

This block sits after TimeReferenceSynchronizer in the pipeline.
It provides satellite constellation information including orbital parameters,
health status, and configuration data for Galileo satellites.

Almanac Sources:
1. GSC Europa (gsc-europa.eu) - Official Galileo Service Centre
2. ESA GNSS Service Centre (gssc.esa.int) - FTP/Web GNSS repository

Data Formats Supported:
- YUMA: Standard almanac format used by many GNSS receivers
- SEM: Standard Exchange Medium format
- XML: Galileo-specific almanac format
"""

from __future__ import annotations

import os
import re
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
import json
import xml.etree.ElementTree as ET

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
from utils.skyplot import generate_skyplot, generate_skyplot_tle

################################################ CONSTANTS ################################################

MODULE_FILE = "AlmanacManagement"

# Galileo Almanac Sources
ALMANAC_SOURCES = {
    "gsc_europa": {
        "name": "GSC Europa (Official)",
        "base_url": "https://www.gsc-europa.eu/sites/default/files/sites/all/files/",
        "almanac_list_url": "https://www.gsc-europa.eu/gsc-products/almanac",
        "description": "Official Galileo Service Centre - Current and historical almanacs",
        "requires_auth": False,
        "formats": ["yuma", "xml"],
    },
    "esa_gssc": {
        "name": "ESA GNSS Service Centre",
        "base_url": "https://gssc.esa.int/products/almanac/",
        "ftp_url": "ftp://gssc.esa.int/gnss/products/almanac/",
        "description": "ESA official GNSS repository with almanac archive",
        "requires_auth": False,
        "formats": ["yuma", "sem"],
    },
}

# Galileo Satellite PRNs (E01-E36)
GALILEO_PRNS = [f"E{i:02d}" for i in range(1, 37)]

# Galileo orbital parameters (nominal)
GALILEO_SEMI_MAJOR_AXIS_KM = 29600.318  # km
GALILEO_ORBITAL_PERIOD_SEC = 14 * 3600 + 4 * 60 + 42  # ~14h 4m 42s
GALILEO_ORBIT_RADIUS_M = 29600318  # meters

# Simulated Galileo almanac data (based on actual constellation circa 2024)
# Used when online sources are unavailable
# Galileo: 3 orbital planes separated by 120° RAAN, 8 satellites per plane spaced ~45° apart
# RAAN values in semicircles: Plane A=0, Plane B=0.667, Plane C=1.333 (120° apart)
# mean_anom spaced 0.25 semicircles (~45°) apart within each plane
# Week 1381 = February 2026
import math
_PI = math.pi

SIMULATED_ALMANAC = [
    # Plane A satellites (RAAN = 0 semicircles = 0°)
    {"id": "E01", "sqrt(a)": 5440.588, "eccentricity": 0.000173, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 0.0, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E02", "sqrt(a)": 5440.588, "eccentricity": 0.000191, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 0.25, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E03", "sqrt(a)": 5440.588, "eccentricity": 0.000095, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 0.5, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E04", "sqrt(a)": 5440.588, "eccentricity": 0.000112, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 0.75, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E05", "sqrt(a)": 5440.588, "eccentricity": 0.000143, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 1.0, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E07", "sqrt(a)": 5440.588, "eccentricity": 0.000234, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 1.25, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E08", "sqrt(a)": 5440.588, "eccentricity": 0.000156, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 1.5, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E09", "sqrt(a)": 5440.588, "eccentricity": 0.000189, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.0, "argument_of_perigee": 0.0, "mean_anom": 1.75, "time_of_applicability": 0, "week": 1381, "health": 0},
    # Plane B satellites (RAAN = 0.667 semicircles = 120°)
    {"id": "E11", "sqrt(a)": 5440.588, "eccentricity": 0.000201, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 0.125, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E12", "sqrt(a)": 5440.588, "eccentricity": 0.000167, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 0.375, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E13", "sqrt(a)": 5440.588, "eccentricity": 0.000145, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 0.625, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E14", "sqrt(a)": 5440.588, "eccentricity": 0.000178, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 0.875, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E15", "sqrt(a)": 5440.588, "eccentricity": 0.000134, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 1.125, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E18", "sqrt(a)": 5440.588, "eccentricity": 0.000212, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 1.375, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E19", "sqrt(a)": 5440.588, "eccentricity": 0.000198, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 1.625, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E21", "sqrt(a)": 5440.588, "eccentricity": 0.000165, "orbital_inclination": 0.9666, "right_ascen_at_week": 0.667, "argument_of_perigee": 0.0, "mean_anom": 1.875, "time_of_applicability": 0, "week": 1381, "health": 0},
    # Plane C satellites (RAAN = 1.333 semicircles = 240°)
    {"id": "E24", "sqrt(a)": 5440.588, "eccentricity": 0.000187, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 0.0625, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E25", "sqrt(a)": 5440.588, "eccentricity": 0.000156, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 0.3125, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E26", "sqrt(a)": 5440.588, "eccentricity": 0.000223, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 0.5625, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E27", "sqrt(a)": 5440.588, "eccentricity": 0.000178, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 0.8125, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E30", "sqrt(a)": 5440.588, "eccentricity": 0.000145, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 1.0625, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E31", "sqrt(a)": 5440.588, "eccentricity": 0.000201, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 1.3125, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E33", "sqrt(a)": 5440.588, "eccentricity": 0.000167, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 1.5625, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E34", "sqrt(a)": 5440.588, "eccentricity": 0.000189, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 1.8125, "time_of_applicability": 0, "week": 1381, "health": 0},
    {"id": "E36", "sqrt(a)": 5440.588, "eccentricity": 0.000212, "orbital_inclination": 0.9666, "right_ascen_at_week": 1.333, "argument_of_perigee": 0.0, "mean_anom": 0.1875, "time_of_applicability": 0, "week": 1381, "health": 0},
]

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
    console_log("AlmanacManagement", msg)




################################################# ALMANAC DOWNLOADING #################################################

def fetch_available_almanacs_gsc_europa() -> List[str]:
    """
    Fetch available almanac dates from GSC Europa website.
    
    Returns:
        List of date strings (YYYY-MM-DD) for available almanacs, sorted newest first
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*'
    }
    try:
        url = "https://www.gsc-europa.eu/gsc-products/almanac"
        response = requests.get(url, timeout=30, headers=headers)
        if response.status_code != 200:
            return []
        
        # Parse the HTML to find almanac links
        # Links are in format: href="/sites/default/files/sites/all/files/YYYY-MM-DD.xml"
        # or with different separators like YYYY_MM_DD.xml
        pattern = r'href="[^"]*files/(\d{4}[-_]\d{2}[-_]\d{2})\.xml"'
        matches = re.findall(pattern, response.text)
        
        # Normalize dates (replace underscores with dashes)
        normalized_dates = [m.replace('_', '-') for m in matches]
        
        # Remove duplicates and sort by date (newest first)
        unique_dates = sorted(set(normalized_dates), reverse=True)
        _debug(f"Found {len(unique_dates)} available almanacs on GSC Europa")
        return unique_dates
        
    except Exception as e:
        _debug(f"Error fetching almanac list: {e}")
        return []


# Browser headers for GSC Europa requests
GSC_EUROPA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*'
}


def download_almanac_gsc_europa(target_datetime: datetime, output_dir: Path, 
                                 format_type: str = "xml") -> Tuple[Optional[Path], Optional[str]]:
    """
    Download Galileo almanac from GSC Europa.
    
    GSC Europa provides almanacs in XML format only.
    URL format: https://www.gsc-europa.eu/sites/default/files/sites/all/files/YYYY-MM-DD.xml
    Almanacs are typically updated every 3-4 days.
    
    Args:
        target_datetime: Target date/time for the almanac
        output_dir: Directory to save the downloaded file
        format_type: Format type (only "xml" is supported by GSC Europa)
        
    Returns:
        Tuple of (file_path, error_message)
    """
    _debug(f"Downloading almanac from GSC Europa for {target_datetime.isoformat()}")
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # GSC Europa URL pattern: YYYY-MM-DD.xml
    base_url = "https://www.gsc-europa.eu/sites/default/files/sites/all/files"
    
    # Try to find the closest almanac to target date
    # First, try exact date
    date_str = target_datetime.strftime("%Y-%m-%d")
    url = f"{base_url}/{date_str}.xml"
    filename = f"galileo_almanac_{date_str.replace('-', '')}.xml"
    
    _debug(f"Trying URL: {url}")
    try:
        response = requests.get(url, timeout=30, headers=GSC_EUROPA_HEADERS)
        if response.status_code == 200 and len(response.content) > 100 and b'signalData' in response.content:
            output_path = output_dir / filename
            with open(output_path, 'wb') as f:
                f.write(response.content)
            _debug(f"Successfully downloaded almanac to {output_path}")
            return output_path, None
    except requests.RequestException as e:
        _debug(f"Request failed: {e}")
    
    # Try previous days (almanacs are released every 3-4 days)
    for days_back in range(1, 14):
        check_date = target_datetime - timedelta(days=days_back)
        date_str = check_date.strftime("%Y-%m-%d")
        url = f"{base_url}/{date_str}.xml"
        
        _debug(f"Trying URL: {url}")
        try:
            response = requests.get(url, timeout=30, headers=GSC_EUROPA_HEADERS)
            if response.status_code == 200 and len(response.content) > 100 and b'signalData' in response.content:
                filename = f"galileo_almanac_{date_str.replace('-', '')}.xml"
                output_path = output_dir / filename
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                _debug(f"Successfully downloaded almanac from {date_str} to {output_path}")
                return output_path, None
        except requests.RequestException:
            continue
    
    # If no recent almanac found, try to get the most recent available
    _debug("Trying to fetch available almanac list...")
    available = fetch_available_almanacs_gsc_europa()
    if available:
        # Use the most recent available
        most_recent = available[0]
        url = f"{base_url}/{most_recent}.xml"
        _debug(f"Trying most recent available: {url}")
        try:
            response = requests.get(url, timeout=30, headers=GSC_EUROPA_HEADERS)
            if response.status_code == 200 and len(response.content) > 100:
                filename = f"galileo_almanac_{most_recent.replace('-', '')}.xml"
                output_path = output_dir / filename
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                _debug(f"Successfully downloaded most recent almanac ({most_recent}) to {output_path}")
                return output_path, None
        except requests.RequestException as e:
            return None, f"Failed to download almanac: {e}"
    
    return None, "No almanac available from GSC Europa for the requested date range"


def download_almanac_esa_gssc(target_datetime: datetime, output_dir: Path,
                               format_type: str = "yuma") -> Tuple[Optional[Path], Optional[str]]:
    """
    Download Galileo almanac from ESA GNSS Service Centre.
    
    The ESA GSSC provides almanacs via FTP and HTTP.
    
    Args:
        target_datetime: Target date/time for the almanac
        output_dir: Directory to save the downloaded file
        format_type: "yuma" or "sem"
        
    Returns:
        Tuple of (file_path, error_message)
    """
    _debug(f"Downloading almanac from ESA GSSC for {target_datetime.isoformat()}")
    
    year = target_datetime.year
    doy = target_datetime.timetuple().tm_yday
    
    try:
        # ESA GSSC almanac URL patterns
        # Format: https://gssc.esa.int/products/almanac/YYYY/DDD/
        base_url = f"https://gssc.esa.int/products/almanac/{year}/{doy:03d}/"
        
        # Alternative patterns
        date_str = target_datetime.strftime("%Y%m%d")
        possible_urls = [
            f"https://gssc.esa.int/products/almanac/galileo_{date_str}.alm",
            f"https://gssc.esa.int/products/almanac/{year}/GAL_{doy:03d}.alm",
            f"https://gssc.esa.int/products/almanac/{year}/{doy:03d}/gal.alm",
        ]
        
        filename = f"galileo_almanac_{date_str}_esa.{format_type}"
        
        last_error = None
        for url in possible_urls:
            _debug(f"Trying URL: {url}")
            try:
                response = requests.get(url, timeout=30)
                if response.status_code == 200 and len(response.content) > 100:
                    output_path = output_dir / filename
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    with open(output_path, 'wb') as f:
                        f.write(response.content)
                    
                    _debug(f"Successfully downloaded almanac to {output_path}")
                    return output_path, None
                else:
                    last_error = f"HTTP {response.status_code}"
            except requests.RequestException as e:
                last_error = str(e)
        
        return None, f"Could not download from ESA GSSC: {last_error}"
        
    except Exception as e:
        return None, f"Error downloading from ESA GSSC: {str(e)}"


################################################# ALMANAC PARSING #################################################

def parse_yuma_almanac(file_path: Path) -> List[Dict[str, Any]]:
    """
    Parse a YUMA format almanac file.
    
    YUMA format contains orbital parameters for each satellite:
    - ID, Health, Eccentricity, Time of Applicability
    - Orbital Inclination, Rate of Right Ascension
    - SQRT(A), Right Ascension, Argument of Perigee
    - Mean Anomaly, Af0, Af1, Week
    
    Args:
        file_path: Path to the YUMA almanac file
        
    Returns:
        List of satellite almanac dictionaries
    """
    satellites = []
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Split by satellite entries (each starts with "******")
        entries = re.split(r'\*{4,}.*?\n', content)
        
        for entry in entries:
            if not entry.strip():
                continue
            
            sat_data = {}
            lines = entry.strip().split('\n')
            
            for line in lines:
                line = line.strip()
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().lower().replace(' ', '_')
                    value = value.strip()
                    
                    # Try to convert to number
                    try:
                        if '.' in value or 'E' in value.upper():
                            sat_data[key] = float(value)
                        else:
                            sat_data[key] = int(value)
                    except ValueError:
                        sat_data[key] = value
            
            if sat_data:
                satellites.append(sat_data)
        
        _debug(f"Parsed {len(satellites)} satellites from YUMA almanac")
        return satellites
        
    except Exception as e:
        _debug(f"Error parsing YUMA almanac: {str(e)}")
        return []


def parse_xml_almanac(file_path: Path) -> List[Dict[str, Any]]:
    """
    Parse a Galileo XML format almanac file from GSC Europa.
    
    GSC Europa XML format parameters:
    - aSqRoot: Delta from nominal sqrt(a), where a_nominal = 29600318 m
    - deltai: Delta from nominal inclination (56° = 0.311111 semi-circles)
    - omega0: Right ascension in semi-circles
    - omegaDot: Rate of RAAN in semi-circles/s
    - w: Argument of perigee in semi-circles
    - m0: Mean anomaly in semi-circles
    - t0a: Time of applicability in seconds
    - wna: Week number modulo 4 (will be converted to full week using issueDate)
    
    Args:
        file_path: Path to the XML almanac file
        
    Returns:
        List of satellite almanac dictionaries with converted values for skyplot
    """
    satellites = []
    
    # Nominal values for Galileo
    NOMINAL_SQRT_A = 5440.588  # sqrt(29600318 m) in meters^0.5
    NOMINAL_INCLINATION = 0.311111  # 56° in semi-circles
    
    # GST epoch: August 22, 1999, 00:00:00 UTC
    GST_EPOCH = datetime(1999, 8, 22, tzinfo=timezone.utc)
    
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # Extract issueDate from header to compute full week number from wna
        issue_wn = None
        issue_date_elem = root.find('.//issueDate')
        if issue_date_elem is not None and issue_date_elem.text:
            try:
                issue_date_str = issue_date_elem.text.strip()
                # Parse ISO format: 2026-02-06T09:46:32.0Z
                if issue_date_str.endswith('Z'):
                    issue_date_str = issue_date_str[:-1] + '+00:00'
                issue_dt = datetime.fromisoformat(issue_date_str)
                # Convert to GST week
                delta = issue_dt - GST_EPOCH
                issue_wn = int(delta.days // 7)
            except Exception:
                issue_wn = None
        
        # Find all svAlmanac elements (GSC Europa format)
        for sv_almanac in root.iter('svAlmanac'):
            sat_data = {}
            
            # Mark source format - values will be stored in RADIANS
            sat_data['_source'] = 'gsc_europa'
            sat_data['_units'] = 'radians'
            
            # Get SVID
            svid_elem = sv_almanac.find('SVID')
            if svid_elem is not None and svid_elem.text:
                svid = svid_elem.text.strip()
                sat_data['id'] = f"E{int(svid):02d}"
                sat_data['svid'] = int(svid)
            else:
                continue  # Skip if no SVID
            
            # Parse almanac data
            almanac_elem = sv_almanac.find('almanac')
            if almanac_elem is not None:
                # aSqRoot (delta from nominal)
                asqroot_elem = almanac_elem.find('aSqRoot')
                if asqroot_elem is not None and asqroot_elem.text:
                    delta_sqrt_a = float(asqroot_elem.text.strip())
                    sat_data['sqrt(a)'] = NOMINAL_SQRT_A + delta_sqrt_a
                    sat_data['delta_sqrt_a'] = delta_sqrt_a
                
                # eccentricity
                ecc_elem = almanac_elem.find('ecc')
                if ecc_elem is not None and ecc_elem.text:
                    sat_data['eccentricity'] = float(ecc_elem.text.strip())
                
                # deltai (delta from 56° nominal inclination) - Convert to RADIANS
                deltai_elem = almanac_elem.find('deltai')
                if deltai_elem is not None and deltai_elem.text:
                    delta_i = float(deltai_elem.text.strip())
                    inclination_sc = NOMINAL_INCLINATION + delta_i  # semicircles
                    sat_data['orbital_inclination'] = inclination_sc * math.pi  # Convert to radians
                    sat_data['delta_inclination'] = delta_i
                
                # omega0 (RAAN in semi-circles) - Convert to RADIANS
                omega0_elem = almanac_elem.find('omega0')
                if omega0_elem is not None and omega0_elem.text:
                    omega0_sc = float(omega0_elem.text.strip())
                    sat_data['right_ascen_at_week'] = omega0_sc * math.pi  # Convert to radians
                
                # omegaDot (rate of RAAN in semi-circles/s) - Convert to RADIANS/s
                omegadot_elem = almanac_elem.find('omegaDot')
                if omegadot_elem is not None and omegadot_elem.text:
                    omegadot_sc = float(omegadot_elem.text.strip())
                    sat_data['rate_of_right_ascen'] = omegadot_sc * math.pi  # Convert to rad/s
                
                # w (argument of perigee in semi-circles) - Convert to RADIANS
                w_elem = almanac_elem.find('w')
                if w_elem is not None and w_elem.text:
                    w_sc = float(w_elem.text.strip())
                    sat_data['argument_of_perigee'] = w_sc * math.pi  # Convert to radians
                
                # m0 (mean anomaly in semi-circles) - Convert to RADIANS
                m0_elem = almanac_elem.find('m0')
                if m0_elem is not None and m0_elem.text:
                    m0_sc = float(m0_elem.text.strip())
                    sat_data['mean_anom'] = m0_sc * math.pi  # Convert to radians
                
                # af0, af1 (clock corrections)
                af0_elem = almanac_elem.find('af0')
                if af0_elem is not None and af0_elem.text:
                    sat_data['af0'] = float(af0_elem.text.strip())
                
                af1_elem = almanac_elem.find('af1')
                if af1_elem is not None and af1_elem.text:
                    sat_data['af1'] = float(af1_elem.text.strip())
                
                # t0a (time of applicability)
                t0a_elem = almanac_elem.find('t0a')
                if t0a_elem is not None and t0a_elem.text:
                    sat_data['time_of_applicability'] = int(float(t0a_elem.text.strip()))
                
                # wna (week number modulo 4)
                wna_elem = almanac_elem.find('wna')
                if wna_elem is not None and wna_elem.text:
                    wna = int(float(wna_elem.text.strip()))
                    sat_data['wna'] = wna
                    # Compute actual week from wna (mod 4) using issueDate week
                    # Formula: actual_week = issue_wn - (issue_wn % 4) + wna
                    if issue_wn is not None:
                        actual_week = issue_wn - (issue_wn % 4) + wna
                        # Handle edge case: if wna > issue_wn % 4, we might need previous rollover
                        if wna > (issue_wn % 4):
                            actual_week -= 4
                        sat_data['week'] = actual_week
                    else:
                        sat_data['week'] = wna  # Fallback if no issueDate
                
                # iod (issue of data)
                iod_elem = almanac_elem.find('iod')
                if iod_elem is not None and iod_elem.text:
                    sat_data['iod'] = int(float(iod_elem.text.strip()))
            
            # Parse signal status (health)
            health = 0
            fnav_status = sv_almanac.find('svFNavSignalStatus')
            if fnav_status is not None:
                status_e5a = fnav_status.find('statusE5a')
                if status_e5a is not None and status_e5a.text:
                    health = int(float(status_e5a.text.strip()))
            
            inav_status = sv_almanac.find('svINavSignalStatus')
            if inav_status is not None:
                status_e1b = inav_status.find('statusE1B')
                if status_e1b is not None and status_e1b.text:
                    h = int(float(status_e1b.text.strip()))
                    if h != 0:
                        health = h
                status_e5b = inav_status.find('statusE5b')
                if status_e5b is not None and status_e5b.text:
                    h = int(float(status_e5b.text.strip()))
                    if h != 0:
                        health = h
            
            sat_data['health'] = health
            
            if sat_data.get('sqrt(a)') is not None:  # Only add if we have valid orbital data
                satellites.append(sat_data)
        
        _debug(f"Parsed {len(satellites)} satellites from GSC Europa XML almanac")
        return satellites
        
    except Exception as e:
        _debug(f"Error parsing XML almanac: {str(e)}")
        import traceback
        _debug(traceback.format_exc())
        return []


################################################# MAIN PIPELINE #################################################

def _run_pipeline_legacy(
    callback_status=None,
    callback_result=None,
    *,
    scheduled_time: str = "",
    do_skyplot: bool = True,
    user_lat: float = 41.5007,
    user_lon: float = 2.1136,
    **kwargs
) -> Dict[str, Any]:
    """
    Execute the Almanac Management pipeline.
    
    Downloads Galileo almanac data from configured sources,
    parses the data, provides satellite orbital information,
    and optionally generates a skyplot.
    
    Args:
        callback_status: Function to report status updates
        callback_result: Function to report results
        scheduled_time: ISO format datetime for skyplot (from ConfigInit's scheduled_time_doy)
        do_skyplot: Whether to generate skyplot
        user_lat: User latitude for skyplot (degrees)
        user_lon: User longitude for skyplot (degrees)
        **kwargs: Additional arguments
        
    Returns:
        Dictionary containing almanac data and metadata
    """
    def report_status(msg: str, progress: float = 0.0):
        _debug(msg)
        if callable(callback_status):
            callback_status(msg, progress)
    
    def report_result(key: str, value: Any):
        if callable(callback_result):
            callback_result(key, value)
    
    # Load configuration
    config = load_config()
    
    # Get parameters
    source = coerce_string(config.get("almanac_source", "gsc_europa"))
    format_type = coerce_string(config.get("almanac_format", "yuma"))
    use_current_time = coerce_bool(config.get("use_current_time", True))
    target_datetime_str = coerce_string(config.get("target_datetime", ""))
    save_almanac = coerce_bool(config.get("save_almanac_file", True))
    
    # Determine target datetime for almanac download (current time)
    target_dt = datetime.now(timezone.utc)
    if not use_current_time and target_datetime_str:
        try:
            target_dt = datetime.fromisoformat(target_datetime_str.replace('Z', '+00:00'))
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            report_status(f"Invalid datetime, using current time: {target_dt.isoformat()}")
    
    # Determine skyplot datetime: use scheduled_time if provided, otherwise current time
    skyplot_dt = target_dt
    if scheduled_time:
        try:
            skyplot_dt = datetime.fromisoformat(scheduled_time.replace('Z', '+00:00'))
            if skyplot_dt.tzinfo is None:
                skyplot_dt = skyplot_dt.replace(tzinfo=timezone.utc)
            report_status(f"Skyplot scheduled for: {skyplot_dt.isoformat()}", 0.05)
        except (ValueError, AttributeError):
            skyplot_dt = target_dt
    
    report_status(f"Target datetime: {target_dt.isoformat()}", 0.1)
    report_result("target_datetime", target_dt.isoformat())
    
    # Calculate GST
    wn, tow = GalileoTime.from_datetime(target_dt).week_tow
    report_result("gst_week", wn)
    report_result("gst_tow", tow)
    
    # Prepare output directory
    almanac_dir = get_results_subdir("almanac")
    
    report_status(f"Downloading almanac from {source}...", 0.2)
    
    # Download almanac based on source
    almanac_path = None
    error_msg = None
    
    if source == "gsc_europa":
        almanac_path, error_msg = download_almanac_gsc_europa(target_dt, almanac_dir, format_type)
    elif source == "esa_gssc":
        almanac_path, error_msg = download_almanac_esa_gssc(target_dt, almanac_dir, format_type)
    else:
        error_msg = f"Unknown almanac source: {source}"
    
    # If primary source failed, try the other
    if almanac_path is None:
        report_status(f"Primary source failed: {error_msg}. Trying alternative...", 0.4)
        
        alt_source = "esa_gssc" if source == "gsc_europa" else "gsc_europa"
        if alt_source == "gsc_europa":
            almanac_path, error_msg = download_almanac_gsc_europa(target_dt, almanac_dir, format_type)
        else:
            almanac_path, error_msg = download_almanac_esa_gssc(target_dt, almanac_dir, format_type)
    
    # Parse almanac if downloaded
    satellites_data = []
    using_simulated = False
    if almanac_path and almanac_path.exists():
        report_status("Parsing almanac data...", 0.6)
        report_result("almanac_file", str(almanac_path))
        report_result("download_status", "success")
        
        if format_type == "yuma":
            satellites_data = parse_yuma_almanac(almanac_path)
        elif format_type == "xml":
            satellites_data = parse_xml_almanac(almanac_path)
        
        report_result("satellites_count", len(satellites_data))
    else:
        # Fallback to simulated almanac data for skyplot generation
        report_status("Using simulated almanac data for skyplot...", 0.6)
        report_result("download_status", "failed_using_simulated")
        report_result("download_error", error_msg or "Unknown error")
        satellites_data = SIMULATED_ALMANAC.copy()
        using_simulated = True
        report_result("satellites_count", len(satellites_data))
        report_result("data_source", "simulated")
    
    # Generate summary
    report_status("Generating almanac summary...", 0.8)
    
    healthy_count = 0
    unhealthy_count = 0
    
    for sat in satellites_data:
        health = sat.get('health', sat.get('svhealth', 0))
        if health == 0:
            healthy_count += 1
        else:
            unhealthy_count += 1
    
    report_result("healthy_satellites", healthy_count)
    report_result("unhealthy_satellites", unhealthy_count)
    
    # Save parsed data as JSON
    if satellites_data and save_almanac:
        json_path = almanac_dir / f"galileo_almanac_{target_dt.strftime('%Y%m%d')}.json"
        with open(json_path, 'w') as f:
            json.dump({
                "target_datetime": target_dt.isoformat(),
                "gst_week": wn,
                "gst_tow": tow,
                "source": source,
                "satellites": satellites_data
            }, f, indent=2)
        report_result("json_output", str(json_path))
    
    # Generate skyplot if requested
    skyplot_result = None
    skyplot_path = None
    if do_skyplot:
        report_status(f"Generating skyplot for {skyplot_dt.isoformat()}...", 0.9)
        try:
            skyplot_path = almanac_dir / f"skyplot_{skyplot_dt.strftime('%Y%m%d_%H%M%S')}.png"
            
            # Try almanac-based skyplot first if we have data from GSC Europa
            if satellites_data and not using_simulated:
                _debug(f"Using GSC Europa almanac data for skyplot ({len(satellites_data)} satellites)")
                skyplot_result = generate_skyplot(
                    satellites=satellites_data,
                    target_dt=skyplot_dt,
                    user_lat=user_lat,
                    user_lon=user_lon,
                    user_alt=0.0,
                    min_elevation=5.0,
                    output_path=skyplot_path
                )
            
            # If almanac didn't work or no data, try TLE-based skyplot
            if skyplot_result is None or skyplot_result.get('error') or skyplot_result.get('visible_count', 0) == 0:
                _debug("Almanac skyplot failed or unavailable, trying TLE-based skyplot")
                skyplot_result = generate_skyplot_tle(
                    target_dt=skyplot_dt,
                    user_lat=user_lat,
                    user_lon=user_lon,
                    user_alt=0.0,
                    min_elevation=5.0,
                    output_path=skyplot_path,
                    include_gps=True
                )
            
            report_result("skyplot_path", str(skyplot_path))
            report_result("visible_satellites", skyplot_result.get("visible_count", 0))
            report_result("galileo_count", skyplot_result.get("galileo_count", skyplot_result.get("visible_count", 0)))
            report_result("gps_count", skyplot_result.get("gps_count", 0))
            report_status(f"Skyplot generated: {skyplot_result.get('visible_count', 0)} satellites visible", 0.95)
        except Exception as e:
            _debug(f"Error generating skyplot: {e}")
            report_status(f"Skyplot generation failed: {e}", 0.95)
    
    report_status("Almanac management complete", 1.0)
    
    return {
        "success": almanac_path is not None or using_simulated,
        "target_datetime": target_dt.isoformat(),
        "skyplot_datetime": skyplot_dt.isoformat(),
        "gst_week": wn,
        "gst_tow": tow,
        "almanac_file": str(almanac_path) if almanac_path else None,
        "satellites_count": len(satellites_data),
        "healthy_satellites": healthy_count,
        "unhealthy_satellites": unhealthy_count,
        "satellites": satellites_data,
        "skyplot": skyplot_result,
        "skyplot_path": str(skyplot_path) if skyplot_path else None,
        "using_simulated_data": using_simulated,
        "error": error_msg if not using_simulated else None,
    }


############################################ PIPELINE EXECUTION (CONTRACT 1.0) ############################################

from utils.snapshot_naming import (
    parse_scheduled_time_doy as _parse_doy,
    extract_location_from_snapshot_filename as _extract_latlon,
)


def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: (none — all upstream values come from globals)
    produces: almanac_visible_satellites, user_lat, user_lon
    """
    g = dict(globals or {})

    # Derive scheduled_time (ISO) from DOY HH:MM:SS in ConfigInit. The block
    # used to receive this already parsed; now it parses it itself.
    _, scheduled_iso = _parse_doy(g.get("scheduled_time_doy") or "0")

    # Resolve user position: prefer the snapshot filename if it carries the
    # NN[NS]DDDD_NN[EW]DDDD token, else use this block's own config defaults.
    rf_path = g.get("rf_input_path_e6") or g.get("rf_input_path_e1") or ""
    snap_lat, snap_lon = _extract_latlon(rf_path)
    own = config or load_config()
    user_lat = float(snap_lat) if snap_lat is not None else float(own.get("user_lat", 41.5007))
    user_lon = float(snap_lon) if snap_lon is not None else float(own.get("user_lon", 2.1136))

    raw = _run_pipeline_legacy(
        scheduled_time=scheduled_iso,
        do_skyplot=True,
        user_lat=user_lat,
        user_lon=user_lon,
    )

    skyplot = raw.get("skyplot") or {}
    visible = sorted(
        skyplot.get("visible_satellites", []),
        key=lambda s: s.get("elevation", 0),
        reverse=True,
    )

    skyplot_path = raw.get("skyplot_path")
    return {
        "outputs": {
            "almanac_visible_satellites": visible,
            "user_lat": user_lat,
            "user_lon": user_lon,
        },
        "artifacts": {"skyplot": skyplot_path} if skyplot_path else {},
        "summary": {
            "satellites_count": raw.get("satellites_count"),
            "healthy": raw.get("healthy_satellites"),
            "unhealthy": raw.get("unhealthy_satellites"),
            "visible_count": len(visible),
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"
