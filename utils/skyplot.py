"""
Skyplot Generation Module
-------------------------
Generates polar skyplots showing satellite visibility from a given location
and time using almanac data or TLEs.
"""

from __future__ import annotations

import math
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from utils.time_galileo import GST_EPOCH, GalileoTime

# Try to import skyfield for TLE-based calculations
try:
    from skyfield.api import load, wgs84, EarthSatellite
    from skyfield.timelib import Time
    SKYFIELD_AVAILABLE = True
except ImportError:
    SKYFIELD_AVAILABLE = False

# Constants
GALILEO_GM = 3.986004418e14  # m^3/s^2 (gravitational constant * Earth mass)
GALILEO_OMEGA_E = 7.2921151467e-5  # rad/s (Earth rotation rate)
WGS84_A = 6378137.0  # WGS84 semi-major axis (m)
WGS84_E2 = 0.00669437999014  # WGS84 first eccentricity squared

# Celestrak TLE URLs
CELESTRAK_GALILEO_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=galileo&FORMAT=tle"
CELESTRAK_GPS_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=gps-ops&FORMAT=tle"

# Galileo satellite name to PRN mapping (from NORAD names)
GALILEO_NAME_TO_PRN = {
    "GSAT0101": "E11", "GSAT0102": "E12",  # IOV satellites
    "GSAT0103": "E19", "GSAT0104": "E20",
    "GSAT0201": "E18", "GSAT0202": "E14",  # FOC (some in wrong orbits)
    "GSAT0203": "E26", "GSAT0204": "E22",
    "GSAT0205": "E24", "GSAT0206": "E30",
    "GSAT0207": "E07", "GSAT0208": "E08",
    "GSAT0209": "E09", "GSAT0210": "E01",
    "GSAT0211": "E02", "GSAT0212": "E03",
    "GSAT0213": "E04", "GSAT0214": "E05",
    "GSAT0215": "E21", "GSAT0216": "E25",
    "GSAT0217": "E27", "GSAT0218": "E31",
    "GSAT0219": "E36", "GSAT0220": "E13",
    "GSAT0221": "E15", "GSAT0222": "E33",
    "GSAT0223": "E34", "GSAT0224": "E10",
    "GSAT0225": "E29", "GSAT0226": "E28",
}


def download_tles_celestrak(constellation: str = "galileo") -> List[Tuple[str, str, str]]:
    """
    Download TLEs from Celestrak.
    
    Args:
        constellation: "galileo" or "gps"
        
    Returns:
        List of (name, line1, line2) tuples
    """
    url = CELESTRAK_GALILEO_URL if constellation == "galileo" else CELESTRAK_GPS_URL
    
    try:
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            return []
        
        lines = response.text.strip().split('\n')
        tles = []
        
        i = 0
        while i < len(lines) - 2:
            name = lines[i].strip()
            line1 = lines[i + 1].strip()
            line2 = lines[i + 2].strip()
            
            if line1.startswith('1 ') and line2.startswith('2 '):
                tles.append((name, line1, line2))
                i += 3
            else:
                i += 1
        
        return tles
    except Exception:
        return []


def compute_satellite_positions_from_tle(
    tles: List[Tuple[str, str, str]],
    target_dt: datetime,
    user_lat: float,
    user_lon: float,
    user_alt: float = 0.0,
    min_elevation: float = 5.0,
    constellation: str = "galileo"
) -> List[Dict[str, Any]]:
    """
    Compute satellite positions from TLEs using Skyfield.
    
    Args:
        tles: List of (name, line1, line2) TLE tuples
        target_dt: Target datetime
        user_lat: User latitude in degrees
        user_lon: User longitude in degrees
        user_alt: User altitude in meters
        min_elevation: Minimum elevation to consider visible
        constellation: "galileo" or "gps"
        
    Returns:
        List of satellite position dictionaries
    """
    if not SKYFIELD_AVAILABLE or not tles:
        return []
    
    ts = load.timescale()
    
    # Convert datetime to skyfield time
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)
    t = ts.from_datetime(target_dt)
    
    # User location
    user_location = wgs84.latlon(user_lat, user_lon, elevation_m=user_alt)
    
    satellites = []
    
    for name, line1, line2 in tles:
        try:
            sat = EarthSatellite(line1, line2, name, ts)
            
            # Compute position relative to user
            difference = sat - user_location
            topocentric = difference.at(t)
            
            alt, az, distance = topocentric.altaz()
            elevation = alt.degrees
            azimuth = az.degrees
            
            # Get PRN
            if constellation == "galileo":
                # Extract GSAT number from name
                gsat_match = name.split()[0] if name else ""
                prn = GALILEO_NAME_TO_PRN.get(gsat_match, gsat_match)
            else:
                # GPS: extract PRN from name (e.g., "GPS BIIR-2 (PRN 13)")
                import re
                prn_match = re.search(r'PRN\s*(\d+)', name)
                if prn_match:
                    prn = f"G{int(prn_match.group(1)):02d}"
                else:
                    prn = name[:10]
            
            satellites.append({
                'id': prn,
                'name': name,
                'elevation': elevation,
                'azimuth': azimuth,
                'distance_km': distance.km,
                'visible': elevation >= min_elevation
            })
            
        except Exception:
            continue
    
    return satellites




def lla_to_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> Tuple[float, float, float]:
    """
    Convert Latitude, Longitude, Altitude to ECEF coordinates.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)
    
    N = WGS84_A / math.sqrt(1 - WGS84_E2 * sin_lat**2)
    
    x = (N + alt_m) * cos_lat * cos_lon
    y = (N + alt_m) * cos_lat * sin_lon
    z = (N * (1 - WGS84_E2) + alt_m) * sin_lat
    
    return x, y, z


def ecef_to_lla(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """
    Convert ECEF coordinates to Latitude, Longitude, Altitude.
    """
    lon = math.atan2(y, x)
    
    p = math.sqrt(x**2 + y**2)
    lat = math.atan2(z, p * (1 - WGS84_E2))
    
    for _ in range(10):
        sin_lat = math.sin(lat)
        N = WGS84_A / math.sqrt(1 - WGS84_E2 * sin_lat**2)
        lat_new = math.atan2(z + WGS84_E2 * N * sin_lat, p)
        if abs(lat_new - lat) < 1e-12:
            break
        lat = lat_new
    
    sin_lat = math.sin(lat)
    N = WGS84_A / math.sqrt(1 - WGS84_E2 * sin_lat**2)
    alt = p / math.cos(lat) - N
    
    return math.degrees(lat), math.degrees(lon), alt


def compute_satellite_position_almanac(
    sat_data: Dict[str, Any],
    target_dt: datetime
) -> Optional[Tuple[float, float, float]]:
    """
    Compute satellite ECEF position from almanac orbital parameters.
    
    Supports both YUMA format (semicircles) and GSC Europa format (already in radians).
    
    Args:
        sat_data: Dictionary with orbital parameters from almanac
        target_dt: Target UTC datetime
        
    Returns:
        Tuple of (x, y, z) ECEF coordinates in meters, or None if calculation fails
    """
    try:
        # Check if values are already in radians (GSC Europa format)
        already_radians = sat_data.get('_units') == 'radians'
        
        # Extract orbital parameters
        sqrt_a = sat_data.get('sqrt(a)', sat_data.get('sqrta', sat_data.get('sqrt_a', 5440.588)))
        e = sat_data.get('eccentricity', sat_data.get('e', 0.0))
        
        # Inclination
        i0_raw = sat_data.get('orbital_inclination', sat_data.get('i0', sat_data.get('inclination', 0.9666)))
        # Rate of right ascension
        omega_dot_raw = sat_data.get('rate_of_right_ascen', sat_data.get('omegadot', sat_data.get('omega_dot', -5.36e-9)))
        # Right ascension at week
        omega0_raw = sat_data.get('right_ascen_at_week', sat_data.get('omega0', sat_data.get('right_ascension', 0.0)))
        # Argument of perigee
        omega_raw = sat_data.get('argument_of_perigee', sat_data.get('omega', sat_data.get('perigee', 0.0)))
        # Mean anomaly
        m0_raw = sat_data.get('mean_anom', sat_data.get('m0', sat_data.get('mean_anomaly', 0.0)))
        
        # Time of applicability
        toa = sat_data.get('time_of_applicability', sat_data.get('toa', 0.0))
        week = sat_data.get('week', sat_data.get('gps_week', sat_data.get('galileo_week', 0)))
        
        if already_radians:
            # Values from GSC Europa are already in radians
            i0 = i0_raw
            omega0 = omega0_raw
            omega = omega_raw
            m0 = m0_raw
            omega_dot = omega_dot_raw
        else:
            # YUMA format - need to convert from semicircles to radians
            # For inclination: check if semicircles vs radians vs degrees
            if i0_raw > 0.2 and i0_raw < 1.5:  # semicircles (Galileo ~0.31 or ~0.97)
                i0 = i0_raw * math.pi
            elif i0_raw > 45 and i0_raw < 70:  # degrees
                i0 = math.radians(i0_raw)
            else:
                i0 = i0_raw  # assume radians
            
            # Convert other angles from semicircles to radians
            omega0 = omega0_raw * math.pi if abs(omega0_raw) < 2 else omega0_raw
            omega = omega_raw * math.pi if abs(omega_raw) < 2 else omega_raw
            m0 = m0_raw * math.pi if abs(m0_raw) < 2 else m0_raw
            
            # Rate of right ascension
            omega_dot = omega_dot_raw * math.pi if abs(omega_dot_raw) > 1e-10 else omega_dot_raw
        
        # Semi-major axis
        a = sqrt_a ** 2
        
        # Mean motion
        n0 = math.sqrt(GALILEO_GM / (a ** 3))
        
        # Get target time in GST
        target_wn, target_tow = GalileoTime.from_datetime(target_dt).week_tow
        
        # Time from almanac reference epoch
        # Handle week rollover
        week_diff = target_wn - week
        tk = target_tow - toa + week_diff * 7 * 24 * 3600
        
        # Normalize (-302400 to 302400 seconds)
        if tk > 302400:
            tk -= 604800
        elif tk < -302400:
            tk += 604800
        
        # Mean anomaly at target time
        mk = m0 + n0 * tk
        
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
        
        # Corrected radius
        rk = a * (1 - e * math.cos(ek))
        
        # Positions in orbital plane
        xk_prime = rk * math.cos(phi_k)
        yk_prime = rk * math.sin(phi_k)
        
        # Corrected longitude of ascending node
        omega_k = omega0 + (omega_dot - GALILEO_OMEGA_E) * tk - GALILEO_OMEGA_E * toa
        
        # ECEF coordinates
        x = xk_prime * math.cos(omega_k) - yk_prime * math.cos(i0) * math.sin(omega_k)
        y = xk_prime * math.sin(omega_k) + yk_prime * math.cos(i0) * math.cos(omega_k)
        z = yk_prime * math.sin(i0)
        
        return (x, y, z)
        
    except Exception as e:
        return None


def compute_elevation_azimuth(
    user_lat: float, 
    user_lon: float, 
    user_alt: float,
    sat_ecef: Tuple[float, float, float]
) -> Tuple[float, float]:
    """
    Compute elevation and azimuth from user position to satellite.
    
    Args:
        user_lat: User latitude in degrees
        user_lon: User longitude in degrees
        user_alt: User altitude in meters
        sat_ecef: Satellite ECEF coordinates (x, y, z) in meters
        
    Returns:
        Tuple of (elevation_deg, azimuth_deg)
    """
    # User ECEF position
    user_ecef = lla_to_ecef(user_lat, user_lon, user_alt)
    
    lat_rad = math.radians(user_lat)
    lon_rad = math.radians(user_lon)
    
    # Line of sight vector
    los = np.array([
        sat_ecef[0] - user_ecef[0],
        sat_ecef[1] - user_ecef[1],
        sat_ecef[2] - user_ecef[2]
    ])
    los_norm = np.linalg.norm(los)
    if los_norm < 1e-6:
        return 0.0, 0.0
    los = los / los_norm
    
    # Rotation matrix ECEF to ENU
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)
    
    R = np.array([
        [-sin_lon, cos_lon, 0],
        [-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat],
        [cos_lat * cos_lon, cos_lat * sin_lon, sin_lat]
    ])
    
    # LOS in ENU
    los_enu = R @ los
    
    # Elevation and azimuth
    elevation = math.asin(np.clip(los_enu[2], -1, 1))
    azimuth = math.atan2(los_enu[0], los_enu[1])
    
    if azimuth < 0:
        azimuth += 2 * math.pi
    
    return math.degrees(elevation), math.degrees(azimuth)


def generate_skyplot(
    satellites: List[Dict[str, Any]],
    target_dt: datetime,
    user_lat: float,
    user_lon: float,
    user_alt: float = 0.0,
    min_elevation: float = 5.0,
    output_path: Optional[Path] = None,
    title: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate a polar skyplot showing satellite positions.
    
    Args:
        satellites: List of satellite almanac data dictionaries
        target_dt: Target UTC datetime
        user_lat: User latitude in degrees
        user_lon: User longitude in degrees
        user_alt: User altitude in meters
        min_elevation: Minimum elevation to display (degrees)
        output_path: Path to save the plot image
        title: Plot title
        
    Returns:
        Dictionary with visible satellites data and plot path
    """
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    
    visible_satellites = []
    all_satellites = []
    
    for sat in satellites:
        # Get satellite ID
        sat_id = sat.get('id', sat.get('prn', sat.get('svid', sat.get('satellite_id', 'Unknown'))))
        
        # Skip unhealthy satellites
        health = sat.get('health', sat.get('svhealth', 0))
        if health != 0:
            continue
        
        # Calculate satellite position
        sat_pos = compute_satellite_position_almanac(sat, target_dt)
        if sat_pos is None:
            continue
        
        # Calculate elevation and azimuth
        elev, az = compute_elevation_azimuth(user_lat, user_lon, user_alt, sat_pos)
        
        sat_info = {
            'id': sat_id,
            'elevation': elev,
            'azimuth': az,
            'ecef': sat_pos,
            'visible': elev >= min_elevation
        }
        all_satellites.append(sat_info)
        
        if elev >= min_elevation:
            visible_satellites.append(sat_info)
    
    # Sort visible satellites by elevation descending (highest first)
    visible_satellites.sort(key=lambda s: s['elevation'], reverse=True)
    
    # Create polar plot
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_subplot(111, projection='polar')
    
    # Configure polar plot (skyplot convention: North at top, clockwise azimuth)
    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)  # Clockwise
    
    # Elevation rings (0° at edge, 90° at center)
    ax.set_ylim(0, 90)
    ax.set_yticks([0, 15, 30, 45, 60, 75, 90])
    ax.set_yticklabels(['90°', '75°', '60°', '45°', '30°', '15°', '0°'])
    
    # Azimuth labels
    ax.set_xticks(np.radians([0, 45, 90, 135, 180, 225, 270, 315]))
    ax.set_xticklabels(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'])
    
    # Draw minimum elevation circle
    if min_elevation > 0:
        elev_ring = 90 - min_elevation
        theta_ring = np.linspace(0, 2*np.pi, 100)
        ax.plot(theta_ring, [elev_ring]*100, 'r--', alpha=0.5, linewidth=1, label=f'Min elevation ({min_elevation}°)')
    
    # Plot satellites
    colors = plt.cm.tab20(np.linspace(0, 1, max(len(visible_satellites), 1)))
    
    for i, sat in enumerate(visible_satellites):
        az_rad = np.radians(sat['azimuth'])
        r = 90 - sat['elevation']  # Convert elevation to radius (90° at center)
        
        ax.scatter(az_rad, r, s=200, c=[colors[i % len(colors)]], marker='o', edgecolors='black', linewidth=1, zorder=5)
        
        # Label with satellite ID
        ax.annotate(
            str(sat['id']), 
            (az_rad, r),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=9,
            fontweight='bold'
        )
    
    # Title
    if title is None:
        title = f"Galileo Satellite Skyplot\n{target_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    
    # Info text
    info_text = f"Location: {user_lat:.4f}°N, {user_lon:.4f}°E\nVisible satellites: {len(visible_satellites)}"
    fig.text(0.02, 0.02, info_text, fontsize=10, ha='left', va='bottom',
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    # Legend for satellite count
    ax.legend(loc='upper right', bbox_to_anchor=(1.15, 1.05))
    
    plt.tight_layout()
    
    # Save or return the figure
    plot_path = None
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
        plot_path = str(output_path)
    
    plt.close(fig)
    
    return {
        'visible_count': len(visible_satellites),
        'total_count': len(all_satellites),
        'visible_satellites': visible_satellites,
        'plot_path': plot_path,
        'target_datetime': target_dt.isoformat(),
        'user_location': {
            'latitude': user_lat,
            'longitude': user_lon,
            'altitude': user_alt
        }
    }


def generate_skyplot_from_almanac(
    almanac_path: Path,
    target_dt: datetime,
    user_lat: float,
    user_lon: float,
    user_alt: float = 0.0,
    output_path: Optional[Path] = None,
    min_elevation: float = 5.0
) -> Dict[str, Any]:
    """
    Generate skyplot from an almanac file.
    
    Args:
        almanac_path: Path to almanac file (YUMA or XML)
        target_dt: Target UTC datetime
        user_lat: User latitude in degrees
        user_lon: User longitude in degrees
        user_alt: User altitude in meters
        output_path: Path to save the plot
        min_elevation: Minimum elevation angle (degrees)
        
    Returns:
        Skyplot result dictionary
    """
    from modules.Module_AlmanacManagement import parse_yuma_almanac, parse_xml_almanac
    
    almanac_path = Path(almanac_path)
    
    # Parse almanac based on file extension
    if almanac_path.suffix.lower() in ['.yuma', '.alm']:
        satellites = parse_yuma_almanac(almanac_path)
    elif almanac_path.suffix.lower() == '.xml':
        satellites = parse_xml_almanac(almanac_path)
    else:
        # Try YUMA first
        satellites = parse_yuma_almanac(almanac_path)
        if not satellites:
            satellites = parse_xml_almanac(almanac_path)
    
    if not satellites:
        return {
            'error': f"Could not parse almanac file: {almanac_path}",
            'visible_count': 0,
            'visible_satellites': []
        }
    
    return generate_skyplot(
        satellites=satellites,
        target_dt=target_dt,
        user_lat=user_lat,
        user_lon=user_lon,
        user_alt=user_alt,
        min_elevation=min_elevation,
        output_path=output_path
    )


def generate_skyplot_tle(
    target_dt: datetime,
    user_lat: float,
    user_lon: float,
    user_alt: float = 0.0,
    min_elevation: float = 5.0,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    include_gps: bool = True
) -> Dict[str, Any]:
    """
    Generate skyplot from TLEs downloaded from Celestrak.
    
    This provides accurate satellite positions using SGP4 propagation.
    
    Args:
        target_dt: Target UTC datetime
        user_lat: User latitude in degrees
        user_lon: User longitude in degrees
        user_alt: User altitude in meters
        min_elevation: Minimum elevation angle (degrees)
        output_path: Path to save the plot
        title: Plot title
        include_gps: Whether to include GPS satellites
        
    Returns:
        Skyplot result dictionary
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    
    if not SKYFIELD_AVAILABLE:
        return {
            'error': 'Skyfield not available',
            'visible_count': 0,
            'visible_satellites': []
        }
    
    # Download TLEs
    galileo_tles = download_tles_celestrak("galileo")
    gps_tles = download_tles_celestrak("gps") if include_gps else []
    
    if not galileo_tles and not gps_tles:
        return {
            'error': 'Could not download TLEs from Celestrak',
            'visible_count': 0,
            'visible_satellites': []
        }
    
    # Compute satellite positions
    galileo_sats = compute_satellite_positions_from_tle(
        galileo_tles, target_dt, user_lat, user_lon, user_alt, min_elevation, "galileo"
    )
    gps_sats = compute_satellite_positions_from_tle(
        gps_tles, target_dt, user_lat, user_lon, user_alt, min_elevation, "gps"
    ) if include_gps else []
    
    # Filter visible satellites
    visible_galileo = [s for s in galileo_sats if s['visible']]
    visible_gps = [s for s in gps_sats if s['visible']]
    
    all_visible = visible_galileo + visible_gps
    
    # Create polar plot
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_subplot(111, projection='polar')
    
    # Configure polar plot
    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 90)
    ax.set_yticks([0, 30, 60, 90])
    ax.set_yticklabels(['90°', '60°', '30°', '0°'])
    ax.set_xticks(np.radians([0, 45, 90, 135, 180, 225, 270, 315]))
    ax.set_xticklabels(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'])
    
    # Plot Galileo satellites (blue circles)
    for sat in visible_galileo:
        az_rad = np.radians(sat['azimuth'])
        r = 90 - sat['elevation']
        ax.scatter(az_rad, r, s=150, facecolors='none', 
                   edgecolors='dodgerblue', linewidth=1.5, zorder=5)
        ax.annotate(sat['id'], (az_rad, r), textcoords="offset points",
                    xytext=(5, 5), fontsize=8, color='dodgerblue', fontweight='bold')
    
    # Plot GPS satellites (orange circles)
    for sat in visible_gps:
        az_rad = np.radians(sat['azimuth'])
        r = 90 - sat['elevation']
        ax.scatter(az_rad, r, s=150, facecolors='none',
                   edgecolors='orange', linewidth=1.5, zorder=5)
        ax.annotate(sat['id'], (az_rad, r), textcoords="offset points",
                    xytext=(5, 5), fontsize=8, color='orange', fontweight='bold')
    
    # Title
    if title is None:
        title = f"Galileo skyplot at snapshot time"
    ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    
    # Add elevation label
    ax.set_ylabel('Elevation (°)', labelpad=30)
    
    # Legend
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker='o', color='w', markerfacecolor='none', 
               markeredgecolor='dodgerblue', markersize=10, label='galileo'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='none',
               markeredgecolor='orange', markersize=10, label='gps')
    ]
    ax.legend(handles=legend_elements, loc='upper center', bbox_to_anchor=(0.5, -0.05),
              ncol=2, frameon=False)
    
    plt.tight_layout()
    
    # Save plot
    plot_path = None
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
        plot_path = str(output_path)
    
    plt.close(fig)
    
    return {
        'visible_count': len(all_visible),
        'galileo_count': len(visible_galileo),
        'gps_count': len(visible_gps),
        'total_galileo': len(galileo_sats),
        'total_gps': len(gps_sats),
        'visible_satellites': all_visible,
        'plot_path': plot_path,
        'target_datetime': target_dt.isoformat(),
        'user_location': {
            'latitude': user_lat,
            'longitude': user_lon,
            'altitude': user_alt
        }
    }
