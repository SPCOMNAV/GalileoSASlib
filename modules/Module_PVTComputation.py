"""
Module_PVTComputation.py
-----------------------
Snapshot PVT (Position, Velocity, Time) computation for a Galileo E1B
snapshot receiver, ported from the snaprx MATLAB reference (I. Fernández).

Algorithm overview (Coarse-Time Navigation):
  1. CT-Doppler  – 5-state (X,Y,Z, freq_offset, coarse_time) using
     carrier-Doppler measurements to get a coarse position + time estimate.
  2. CT-PRange   – 5-state (X,Y,Z, clock_bias, coarse_time) with
     code-period ambiguity resolution to get the final position/time.

Both stages use iterative LS (7 iterations, as in the MATLAB reference).

The block receives:
  • correlation_results – code phases (samples) & Doppler (Hz) per satellite
  • ephemerides_data    – broadcast ephemerides + satellite ECEF positions

Coordinate helpers (cart2geo, geod2cart, topocent, tropo, iono) are direct
ports of the Kai Borre / SoftGNSS / snaprx MATLAB originals.
"""

from __future__ import annotations

import math
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json

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

################################################ CONSTANTS ################################################

MODULE_FILE = "PVTComputation"

# WGS84 ellipsoid (index "5" in Borre's tables)
WGS84_A = 6378137.0                          # semi-major axis  [m]
WGS84_F = 1.0 / 298.257223563                # flattening
WGS84_B = WGS84_A * (1.0 - WGS84_F)         # semi-minor axis  [m]
WGS84_E2 = 2.0 * WGS84_F - WGS84_F ** 2     # 1st eccentricity²

# Physical / GNSS
C = 299792458.0                               # speed of light   [m/s]
GM = 3.986004418e14                           # Earth GM         [m³/s²]
OMEGA_E = 7.2921151467e-5                     # Earth rotation   [rad/s]
F_REL = -4.442807633e-10                      # relativistic constant

# Galileo E1 signal
GAL_E1_FREQ_HZ = 1575.42e6                   # E1 carrier       [Hz]
GAL_E1_CODE_CHIPS = 4092                      # E1B primary code chips
GAL_E1_CHIP_RATE = 1.023e6                    # E1B chip rate    [cps]
GAL_E1_CODE_PERIOD_S = GAL_E1_CODE_CHIPS / GAL_E1_CHIP_RATE   # 4 ms
GAL_E1_CODE_PERIOD_MS = GAL_E1_CODE_PERIOD_S * 1e3             # 4.0 ms

# Galileo E6 signal
GAL_E6_FREQ_HZ = 1278.75e6                   # E6 carrier       [Hz]
GAL_E6_CODE_CHIPS = 5115                      # E6C code length
GAL_E6_CHIP_RATE = 5.115e6                    # E6 chip rate     [cps]
GAL_E6_CODE_PERIOD_S = GAL_E6_CODE_CHIPS / GAL_E6_CHIP_RATE   # 1 ms
GAL_E6_CODE_PERIOD_MS = GAL_E6_CODE_PERIOD_S * 1e3             # 1.0 ms

PI = 3.1415926535898                          # GPS/Galileo pi

# Thresholds (from snaprx initSettings)
DOPP_THR = 8.0        # residual threshold for CT-Doppler [m/s]
CODE_THR = 300.0      # RMS residual threshold for CT-PRange [m]
HMAX = 20000.0        # max height above ellipsoid [m]


################################################ SCHEMA / CONFIG ################################################

def load_schema() -> Dict[str, Any]:
    return _load_schema(MODULE_FILE)

def load_config() -> Dict[str, Any]:
    config = _load_config(MODULE_FILE)
    return config if config else get_defaults()

def save_config(config: Dict[str, Any]) -> None:
    _save_config(MODULE_FILE, config)

def get_defaults() -> Dict[str, Any]:
    schema = load_schema()
    defaults = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            fid = field.get("id")
            if fid and "default" in field:
                defaults[fid] = field["default"]
    return defaults

################################################ LOGGING ################################################

def _debug(msg: str) -> None:
    console_log("PVTComputation", msg)

################################################ GEO-FUNCTIONS ################################################
# Direct ports of Kai Borre / SoftGNSS / snaprx MATLAB functions.

def cart2geo(X: float, Y: float, Z: float) -> Tuple[float, float, float]:
    """ECEF → geodetic (lat°, lon°, h_m) on WGS-84.  (Borre cart2geo.m)"""
    a = WGS84_A
    f = WGS84_F
    lam = math.atan2(Y, X)
    ex2 = (2.0 - f) * f / ((1.0 - f) ** 2)
    c_ = a * math.sqrt(1.0 + ex2)
    p = math.sqrt(X * X + Y * Y)
    phi = math.atan2(Z, p * (1.0 - (2.0 - f) * f))
    h = 0.1
    for _ in range(20):
        old_h = h
        N = c_ / math.sqrt(1.0 + ex2 * math.cos(phi) ** 2)
        phi = math.atan2(Z, p * (1.0 - (2.0 - f) * f * N / (N + h)))
        h = p / math.cos(phi) - N if abs(math.cos(phi)) > 1e-15 else abs(Z) - WGS84_B
        if abs(h - old_h) < 1e-12:
            break
    return math.degrees(phi), math.degrees(lam), h


def geod2cart(plh: Tuple[float, float, float]) -> np.ndarray:
    """Geodetic (lat°, lon°, h_m) → ECEF [x,y,z].  (Borre geod2cart.m)"""
    lat = math.radians(plh[0])
    lon = math.radians(plh[1])
    h = plh[2]
    a = WGS84_A
    f = WGS84_F
    ex2 = (2.0 - f) * f / ((1.0 - f) ** 2)
    c_ = a * math.sqrt(1.0 + ex2)
    N = c_ / math.sqrt(1.0 + ex2 * math.cos(lat) ** 2)
    x = (N + h) * math.cos(lat) * math.cos(lon)
    y = (N + h) * math.cos(lat) * math.sin(lon)
    z = ((1.0 - f) ** 2 * N + h) * math.sin(lat)
    return np.array([x, y, z])


def togeod(a: float, finv: float, X: float, Y: float, Z: float) -> Tuple[float, float, float]:
    """ECEF → geodetic using Bowring iteration (Borre togeod.m)."""
    rtd = 180.0 / math.pi
    f = 1.0 / finv if finv > 1e-20 else 0.0
    esq = (2.0 - f) * f
    oneesq = 1.0 - esq
    P = math.sqrt(X * X + Y * Y)
    dlambda = math.atan2(Y, X) * rtd if P > 1e-20 else 0.0
    if dlambda < 0:
        dlambda += 360.0
    r = math.sqrt(P * P + Z * Z)
    if r < 1e-20:
        return 0.0, dlambda, 0.0
    sinphi = Z / r
    dphi = math.asin(sinphi)
    h = r - a * (1.0 - sinphi * sinphi / finv)
    for _ in range(10):
        sp = math.sin(dphi)
        cp = math.cos(dphi)
        N_phi = a / math.sqrt(1.0 - esq * sp * sp)
        dP = P - (N_phi + h) * cp
        dZ = Z - (N_phi * oneesq + h) * sp
        h += sp * dZ + cp * dP
        dphi += (cp * dZ - sp * dP) / (N_phi + h)
        if dP * dP + dZ * dZ < 1e-10:
            break
    dphi *= rtd
    return dphi, dlambda, h


def topocent(X: np.ndarray, dx: np.ndarray) -> Tuple[float, float, float]:
    """ECEF vector dx → (Az°, El°, D_m) with origin X.  (Borre topocent.m)"""
    dtr = math.pi / 180.0
    phi, lam, _ = togeod(WGS84_A, 298.257223563, X[0], X[1], X[2])
    cl = math.cos(lam * dtr); sl = math.sin(lam * dtr)
    cb = math.cos(phi * dtr); sb = math.sin(phi * dtr)
    # Rotation matrix ECEF→ENU (transposed)
    F = np.array([[-sl,      -sb * cl,  cb * cl],
                  [ cl,      -sb * sl,  cb * sl],
                  [ 0.0,      cb,       sb     ]])
    loc = F.T @ dx
    E, N, U = loc[0], loc[1], loc[2]
    hor = math.sqrt(E * E + N * N)
    if hor < 1e-20:
        Az, El = 0.0, 90.0
    else:
        Az = math.atan2(E, N) / dtr
        El = math.atan2(U, hor) / dtr
    if Az < 0:
        Az += 360.0
    D = math.sqrt(dx[0] ** 2 + dx[1] ** 2 + dx[2] ** 2)
    return Az, El, D


def tropo(sinel: float, hsta: float, p: float, tkel: float,
          hum: float, hp: float, htkel: float, hhum: float) -> float:
    """Tropospheric range correction [m].  (Borre / Goad & Goodman 1974)"""
    a_e = 6378.137
    b0 = 7.839257e-5
    tlapse = -6.5
    tkhum = tkel + tlapse * (hhum - htkel)
    atkel = 7.5 * (tkhum - 273.15) / (237.3 + tkhum - 273.15)
    e0 = 0.0611 * hum * 10.0 ** atkel
    tksea = tkel - tlapse * htkel
    em = -978.77 / (2.8704e6 * tlapse * 1.0e-5)
    tkelh = tksea + tlapse * hhum
    e0sea = e0 * (tksea / tkelh) ** (4.0 * em)
    tkelp = tksea + tlapse * hp
    psea = p * (tksea / tkelp) ** em
    if sinel < 0:
        sinel = 0.0

    total = 0.0
    for _pass in range(2):
        if _pass == 0:
            refsea = 77.624e-6 / tksea
            htop = 1.1385e-5 / refsea
            refsea *= psea
        else:
            refsea = (371900.0e-6 / tksea - 12.92e-6) / tksea
            htop = 1.1385e-5 * (1255.0 / tksea + 0.05) / refsea
        ref = refsea * ((htop - hsta) / htop) ** 4

        rtop = (a_e + htop) ** 2 - (a_e + hsta) ** 2 * (1.0 - sinel ** 2)
        rtop = math.sqrt(max(rtop, 0.0)) - (a_e + hsta) * sinel
        aa = -sinel / (htop - hsta)
        bb = -b0 * (1.0 - sinel ** 2) / (htop - hsta)
        rn = [rtop ** (i + 2) for i in range(8)]
        alpha = [
            2.0 * aa,
            2.0 * aa ** 2 + 4.0 * bb / 3.0,
            aa * (aa ** 2 + 3.0 * bb),
            aa ** 4 / 5.0 + 2.4 * aa ** 2 * bb + 1.2 * bb ** 2,
            2.0 * aa * bb * (aa ** 2 + 3.0 * bb) / 3.0,
            bb ** 2 * (6.0 * aa ** 2 + 4.0 * bb) * 1.428571e-1,
            aa * bb ** 3 / 2.0 if bb ** 2 > 1e-35 else 0.0,
            bb ** 4 / 9.0       if bb ** 2 > 1e-35 else 0.0,
        ]
        dr = rtop + sum(a * r for a, r in zip(alpha, rn))
        total += dr * ref * 1000.0
    return total


def iono_klobuchar(alpha: List[float], beta: List[float],
                   phi_u: float, lambda_u: float,
                   A: float, E: float, tgps: float) -> float:
    """Klobuchar ionospheric delay [seconds].  (Borre/IS-GPS-200)
    Inputs in semicircles (° / 180) except tgps [s]."""
    psi = 0.0137 / (E + 0.11) - 0.022
    phi_i = max(-0.416, min(phi_u + psi * math.cos(A * PI), 0.416))
    lambda_i = lambda_u + psi * math.sin(A * PI) / math.cos(phi_i * PI)
    phi_m = phi_i + 0.064 * math.cos((lambda_i - 1.617) * PI)
    t = (43200.0 * lambda_i + tgps) % 86400.0
    AMP = max(sum(alpha[j] * phi_m ** j for j in range(4)), 0.0)
    PER = max(sum(beta[j] * phi_m ** j for j in range(4)), 72000.0)
    x = 2.0 * PI * (t - 50400.0) / PER
    Fob = 1.0 + 16.0 * (0.53 - E) ** 3
    if abs(x) < 1.57:
        return Fob * (5e-9 + AMP * (1.0 - x ** 2 / 2.0 + x ** 4 / 24.0))
    return Fob * 5e-9


def check_t(t: float) -> float:
    """Account for TOW rollover (±302400 s).  (Borre check_t.m)"""
    if t > 302400.0:
        t -= 604800.0
    elif t < -302400.0:
        t += 604800.0
    return t

################################################ SATELLITE POSITION ################################################

def _satpos_from_eph(eph: Dict[str, Any], tx_tow: float, rx_tow: float) -> Tuple[np.ndarray, float]:
    """
    Compute satellite ECEF position at *tx_tow* rotated to ECEF frame at
    *rx_tow*, plus satellite clock correction (seconds).

    Port of satpossnap.m (SoftGNSS / Borre / I. Fernández).
    """
    # Clock correction
    dt = check_t(tx_tow - eph["toe"])
    sat_clk = (eph["af2"] * dt + eph["af1"]) * dt + eph["af0"] - eph.get("bgd_e5b_e1", 0.0)
    time = tx_tow - sat_clk

    a = eph["sqrt_a"] ** 2
    tk = check_t(time - eph["toe"])
    n0 = math.sqrt(GM / a ** 3)
    n = n0 + eph["delta_n"]
    M = (eph["m0"] + n * tk) % (2.0 * PI)

    # Kepler iteration
    E = M
    for _ in range(10):
        E_old = E
        E = M + eph["e"] * math.sin(E)
        if abs((E - E_old) % (2.0 * PI)) < 1e-12:
            break
    E %= (2.0 * PI)

    # Relativistic correction
    dtr = F_REL * eph["e"] * eph["sqrt_a"] * math.sin(E)

    # True anomaly
    nu = math.atan2(math.sqrt(1.0 - eph["e"] ** 2) * math.sin(E),
                    math.cos(E) - eph["e"])

    phi = (nu + eph["omega"]) % (2.0 * PI)
    u = phi + eph["cuc"] * math.cos(2.0 * phi) + eph["cus"] * math.sin(2.0 * phi)
    r = a * (1.0 - eph["e"] * math.cos(E)) + eph["crc"] * math.cos(2.0 * phi) + eph["crs"] * math.sin(2.0 * phi)
    i = eph["i0"] + eph["idot"] * tk + eph["cic"] * math.cos(2.0 * phi) + eph["cis"] * math.sin(2.0 * phi)
    Omega = (eph["omega0"] + (eph["omega_dot"] - OMEGA_E) * tk - OMEGA_E * eph["toe"]) % (2.0 * PI)

    xp = r * math.cos(u)
    yp = r * math.sin(u)
    xs = xp * math.cos(Omega) - yp * math.cos(i) * math.sin(Omega)
    ys = xp * math.sin(Omega) + yp * math.cos(i) * math.cos(Omega)
    zs = yp * math.sin(i)

    # Full clock correction with relativistic term
    sat_clk = (eph["af2"] * dt + eph["af1"]) * dt + eph["af0"] - eph.get("bgd_e5b_e1", 0.0) + dtr

    # Rotate ECEF frame from tx-time to rx-time
    tau = rx_tow - tx_tow
    ot = OMEGA_E * tau
    R = np.array([[ math.cos(ot), math.sin(ot), 0.0],
                  [-math.sin(ot), math.cos(ot), 0.0],
                  [ 0.0,          0.0,          1.0]])
    pos = R @ np.array([xs, ys, zs])
    return pos, sat_clk


def _select_best_eph(ephemerides: List[Dict], prn: int, t_tow: float) -> Optional[Dict]:
    """Pick the ephemeris closest to *t_tow* for *prn*."""
    candidates = [e for e in ephemerides if e["prn"] == prn]
    if not candidates:
        return None
    # Prefer healthy, then closest
    healthy = [e for e in candidates if e.get("sv_health", 0) == 0]
    pool = healthy if healthy else candidates
    return min(pool, key=lambda e: abs(check_t(e["toe"] - t_tow)))


def _compute_sat_positions(
    prn_list: List[int],
    ephemerides: List[Dict],
    tx_tows: np.ndarray,
    rx_tow: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute satellite positions (3×N) and clock corrections (N) at given
    tx_tows, rotated to ECEF at rx_tow.
    """
    n = len(prn_list)
    sat_pos = np.zeros((3, n))
    sat_clk = np.zeros(n)
    for i, prn in enumerate(prn_list):
        eph = _select_best_eph(ephemerides, prn, rx_tow)
        if eph is None:
            raise ValueError(f"No ephemeris for PRN E{prn:02d}")
        pos, clk = _satpos_from_eph(eph, tx_tows[i], rx_tow)
        sat_pos[:, i] = pos
        sat_clk[i] = clk
    return sat_pos, sat_clk


################################################ CT-DOPPLER ################################################

def ct_doppler(
    doppler_hz: np.ndarray,
    prn_list: List[int],
    ephemerides: List[Dict],
    initial_sec: float,
    initial_llh: Tuple[float, float, float],
    carrier_freq_hz: float = GAL_E1_FREQ_HZ,
    n_iter: int = 7,
) -> Dict[str, Any]:
    """
    Coarse-Time 5-state Doppler navigation.

    Port of CTDoppler.m (I. Fernández, 2014).

    States: [X, Y, Z, freq_offset, coarse_time_correction]

    Args:
        doppler_hz:   array of carrier-Doppler for each satellite [Hz]
        prn_list:     list of PRN integers
        ephemerides:  list of ephemeris dicts (from RINEX)
        initial_sec:  initial coarse time in *Galileo seconds* (continuous)
        initial_llh:  initial position (lat°, lon°, h_m)
        carrier_freq_hz: carrier frequency (default E1)
        n_iter:       number of iterations (7 per snaprx)

    Returns:
        dict with keys: success, llh, fo, tc_sec, dopSec, residual,
                        dop (GDOP, PDOP, CTDOP)
    """
    nsat = len(prn_list)
    if nsat < 5:
        return {"success": False, "error": f"CT-Doppler needs ≥5 sats, got {nsat}"}

    # Convert Doppler Hz → m/s
    D_meas = doppler_hz * C / carrier_freq_hz

    XYZ0 = geod2cart(initial_llh)
    X = np.zeros(5)
    X[:3] = XYZ0
    best_X = X.copy()
    best_res = float("inf")
    best_dD = None
    best_H = None

    for it in range(n_iter):
        XYZ = X[:3]
        t = initial_sec + X[4]

        t_tow = t % 604800.0
        sat1, _ = _compute_sat_positions(prn_list, ephemerides,
                                         np.full(nsat, t_tow), t_tow)
        sat0, _ = _compute_sat_positions(prn_list, ephemerides,
                                         np.full(nsat, (t_tow - 1.0) % 604800.0),
                                         (t_tow - 1.0) % 604800.0)

        Dpred = np.zeros(nsat)
        rpred1 = np.zeros(nsat)
        rpred0 = np.zeros(nsat)
        for i in range(nsat):
            rpred1[i] = np.linalg.norm(sat1[:, i] - XYZ)
            rpred0[i] = np.linalg.norm(sat0[:, i] - XYZ)
            Dpred[i] = rpred0[i] - rpred1[i] + X[3]  # freq offset in m/s

        dD = D_meas - Dpred

        # H matrix: partial derivatives of Doppler wrt states
        sat2, _ = _compute_sat_positions(prn_list, ephemerides,
                                         np.full(nsat, (t_tow + 1.0) % 604800.0),
                                         (t_tow + 1.0) % 604800.0)
        vel1 = sat1 - sat0
        vel2 = sat2 - sat1

        H = np.zeros((nsat, 5))
        for k in range(nsat):
            dXSat = sat1[:, k] - XYZ
            e1 = dXSat / np.linalg.norm(dXSat)
            a_e = e1 @ vel2[:, k] - e1 @ vel1[:, k]
            edot = (vel1[:, k] - e1 * (-Dpred[k])) / rpred1[k]
            H[k, :3] = -edot
            H[k, 3] = -1.0
            H[k, 4] = a_e

        cur_res = float(np.sqrt(np.mean(dD ** 2)))

        # Track best solution seen so far
        if cur_res < best_res:
            best_res = cur_res
            best_X = X.copy()
            best_dD = dD.copy()
            best_H = H.copy()

        # Early stopping: converged
        if cur_res < DOPP_THR:
            best_X = X.copy()
            best_dD = dD.copy()
            best_H = H.copy()
            best_res = cur_res
            break

        try:
            dX = np.linalg.lstsq(H, -dD, rcond=None)[0]
        except np.linalg.LinAlgError:
            return {"success": False, "error": "Singular Doppler matrix"}
        X += dX

    # Use the best state found across all iterations
    lat, lon, h = cart2geo(best_X[0], best_X[1], best_X[2])
    fo = best_X[3]
    dop_sec = best_X[4] + initial_sec
    res = best_res
    dD = best_dD if best_dD is not None else dD
    H = best_H if best_H is not None else H

    # DOP
    try:
        COV = np.linalg.inv(H.T @ H)
        gdop = math.sqrt(np.trace(COV))
        pdop = math.sqrt(COV[0, 0] + COV[1, 1] + COV[2, 2]) / 1000.0 / (carrier_freq_hz / C)
        ctdop = math.sqrt(COV[4, 4])
    except Exception:
        gdop = pdop = ctdop = float("nan")

    return {
        "success": res < DOPP_THR,
        "llh": (lat, lon, h),
        "fo_mps": fo,
        "tc_sec": best_X[4],
        "dopSec": dop_sec,
        "residual_mps": res,
        "dop": {"GDOP": gdop, "PDOP": pdop, "CTDOP": ctdop},
    }


def ct_doppler_cold_start(
    doppler_hz: np.ndarray,
    prn_list: List[int],
    ephemerides: List[Dict],
    initial_sec: float,
    carrier_freq_hz: float = GAL_E1_FREQ_HZ,
    n_iter: int = 30,
    grid_step_deg: float = 30.0,
) -> Dict[str, Any]:
    """
    Cold-start CT-Doppler: grid search over the Earth's surface.

    Runs ct_doppler from a grid of initial positions and returns
    the solution with the lowest residual.

    Args:
        doppler_hz:      array of carrier-Doppler for each satellite [Hz]
        prn_list:         list of PRN integers
        ephemerides:      list of ephemeris dicts
        initial_sec:      initial coarse time (Galileo seconds)
        carrier_freq_hz:  carrier frequency (default E1)
        n_iter:           iterations per grid point
        grid_step_deg:    grid spacing in degrees (default 30°)

    Returns:
        dict: best ct_doppler result (with extra key 'initial_llh_grid')
    """
    lat_range = np.arange(-60, 61, grid_step_deg)
    lon_range = np.arange(-180, 180, grid_step_deg)

    best_result = None
    best_res = float("inf")
    best_init = None

    for lat0 in lat_range:
        for lon0 in lon_range:
            r = ct_doppler(
                doppler_hz, prn_list, ephemerides, initial_sec,
                (float(lat0), float(lon0), 0.0),
                carrier_freq_hz=carrier_freq_hz,
                n_iter=n_iter,
            )
            res = r.get("residual_mps", float("inf"))
            if res < best_res:
                best_res = res
                best_result = r
                best_init = (float(lat0), float(lon0), 0.0)

    if best_result is not None:
        best_result["initial_llh_grid"] = best_init
    return best_result


################################################ CT-PRANGE ################################################

def ct_prange(
    code_phase_s: np.ndarray,
    prn_list: List[int],
    ephemerides: List[Dict],
    t0_sec: float,
    llh0: Tuple[float, float, float],
    code_period_ms: float = GAL_E1_CODE_PERIOD_MS,
    iono_alpha: Optional[List[float]] = None,
    iono_beta: Optional[List[float]] = None,
    n_iter: int = 7,
    altitude_constraint_m: Optional[float] = None,
    fix_time: bool = False,
) -> Dict[str, Any]:
    """
    Coarse-Time Pseudorange navigation with code-period ambiguity resolution.

    Port of CTPRange.m (I. Fernández, 2014) adapted for Galileo.

    States: [X, Y, Z, clock_bias_m, coarse_time_correction_s]
            When fix_time=True, the 5th state (tc) is fixed at 0,
            reducing to 4 unknowns [X, Y, Z, clock_bias_m].
            Combined with altitude_constraint_m, this enables PVT
            with as few as 3 satellites (3 meas + 1 alt = 4 eqs).

    Args:
        code_phase_s: fractional code-phase per satellite [seconds]
                      (within one code period, range [0, code_period_s))
        prn_list:     list of PRN integers
        ephemerides:  list of ephemeris dicts
        t0_sec:       initial coarse time in Galileo-seconds (continuous)
        llh0:         initial position (lat°, lon°, h_m)
        code_period_ms: code period in ms (4.0 for E1B, 1.0 for E6C)
        iono_alpha:   Klobuchar α coefficients (None → skip iono)
        iono_beta:    Klobuchar β coefficients (None → skip iono)
        n_iter:       number of iterations (7 per snaprx)
        altitude_constraint_m: if set, add an altitude equation to H.
                               Allows PVT with only 4 satellites (5 states
                               = 4 code-phase + 1 altitude constraint).

    Returns dict with keys: success, llh, xyz, clock_bias_m, tc_sec,
        residuals_m, rms_m, PDOP
    """
    nsat = len(prn_list)
    # Determine minimum satellites based on constraints
    if fix_time and altitude_constraint_m is not None:
        min_sats = 3   # 3 meas + altitude = 4 eqs for 4 unknowns [X,Y,Z,bias]
    elif fix_time or altitude_constraint_m is not None:
        min_sats = 4   # 4 meas (+alt or -tc) = enough for 4-5 unknowns
    else:
        min_sats = 5   # 5 meas for 5 unknowns
    if nsat < min_sats:
        return {"success": False,
                "error": f"CT-PRange needs ≥{min_sats} sats, got {nsat}"}

    n_states = 4 if fix_time else 5
    XYZ0 = geod2cart(llh0)
    X = np.zeros(n_states)
    X[:3] = XYZ0  # position
    X[3] = 0.0    # clock bias [m]
    if not fix_time:
        X[4] = 0.0    # coarse time correction [s]

    # Fractional code phases in ms
    zf = code_phase_s * 1e3  # [ms]

    ion = np.zeros(nsat)
    trop_corr = np.zeros(nsat)

    for it in range(n_iter):
        XYZ = X[:3]
        lat, lon, h = cart2geo(XYZ[0], XYZ[1], XYZ[2])
        b = X[3] / C       # bias [s]
        tc = X[4] if not fix_time else 0.0  # coarse time correction [s]

        rx_time = t0_sec + tc
        rx_tow = rx_time % 604800.0
        tx_tows = np.full(nsat, rx_tow)

        # Iterate txTime refinement (3 iterations as in snaprx)
        for _j in range(3):
            sat_pos, sat_dt = _compute_sat_positions(
                prn_list, ephemerides, tx_tows, rx_tow)
            rpred = np.zeros(nsat)
            zpred = np.zeros(nsat)
            for i in range(nsat):
                rpred[i] = np.linalg.norm(sat_pos[:, i] - XYZ) / C
                zpred[i] = rpred[i] - sat_dt[i] + b + trop_corr[i] / C + ion[i]
            tx_tows = (rx_tow - rpred) % 604800.0

        # ------- Elevation / tropo / iono -------
        topo_el = np.zeros(nsat)
        topo_az = np.zeros(nsat)
        for i in range(nsat):
            dX = sat_pos[:, i] - XYZ
            az, el, _ = topocent(XYZ, dX)
            topo_az[i] = az
            topo_el[i] = el
            trop_corr[i] = tropo(math.sin(math.radians(el)),
                                 0.0, 1013.0, 293.0, 50.0, 0.0, 0.0, 0.0)
            if iono_alpha is not None and iono_beta is not None:
                ion[i] = iono_klobuchar(iono_alpha, iono_beta,
                                        lat / 180.0, lon / 180.0,
                                        az / 180.0, el / 180.0, rx_tow)

        # ------- Code-period ambiguity resolution (N) -------
        # Reference satellite = highest elevation
        ref_idx = int(np.argmax(topo_el))
        zf0 = zf[ref_idx]
        zpred0 = zpred[ref_idx]

        # N0 for reference satellite (in integer code periods)
        n0 = round((zpred0 * 1e3 - zf0) / code_period_ms)

        n_arr = np.zeros(nsat, dtype=int)
        z_full = np.zeros(nsat)  # full pseudorange [s]
        dz = np.zeros(nsat)      # residuals [m]
        for k in range(nsat):
            n_arr[k] = round(
                (n0 * code_period_ms + zf0 - zf[k]
                 + (zpred[k] - zpred0) * 1e3) / code_period_ms
            )
            z_full[k] = (zf[k] + n_arr[k] * code_period_ms) / 1e3  # [s]
            dz[k] = (z_full[k] - zpred[k]) * C  # [m]

        # ------- H matrix: n_states states -------
        e = np.zeros((nsat, 3))
        for k in range(nsat):
            d = sat_pos[:, k] - XYZ
            e[k] = d / np.linalg.norm(d)

        if fix_time:
            # 4-state: [X, Y, Z, bias] — no time correction column
            H = np.column_stack([-e, np.ones(nsat)])
        else:
            # 5-state: [X, Y, Z, bias, tc]
            # Satellite velocities (numerical: satpos at rx and rx-1)
            sat1, _ = _compute_sat_positions(prn_list, ephemerides,
                                             np.full(nsat, rx_tow), rx_tow)
            sat0, _ = _compute_sat_positions(prn_list, ephemerides,
                                             np.full(nsat, (rx_tow - 1.0) % 604800.0),
                                             (rx_tow - 1.0) % 604800.0)
            sat_vel = sat1 - sat0
            v_e = np.array([e[k] @ sat_vel[:, k] for k in range(nsat)])
            H = np.column_stack([-e, np.ones(nsat), v_e])

        # ------- Altitude constraint -------
        if altitude_constraint_m is not None:
            lat_r = math.radians(lat)
            lon_r = math.radians(lon)
            n_vec = np.array([math.cos(lat_r) * math.cos(lon_r),
                              math.cos(lat_r) * math.sin(lon_r),
                              math.sin(lat_r)])
            H_alt = np.concatenate([n_vec, np.zeros(n_states - 3)]).reshape(1, n_states)
            dz_alt = np.array([altitude_constraint_m - h])
            H = np.vstack([H, H_alt])
            dz = np.concatenate([dz, dz_alt])

        # ------- Solve -------
        try:
            dX = np.linalg.lstsq(H, dz, rcond=None)[0]
        except np.linalg.LinAlgError:
            return {"success": False, "error": "Singular PRange matrix"}
        X += dX

    # Final position
    lat, lon, h = cart2geo(X[0], X[1], X[2])
    rms = float(np.sqrt(np.mean(dz ** 2)))
    tc = 0.0 if fix_time else X[4]
    xyz = X[:3].tolist()
    bias_m = X[3]

    # PDOP
    try:
        COV = np.linalg.inv(H.T @ H)
        pdop = math.sqrt(COV[0, 0] + COV[1, 1] + COV[2, 2])
    except Exception:
        pdop = float("nan")

    return {
        "success": rms < CODE_THR,
        "llh": (lat, lon, h),
        "xyz": xyz,
        "clock_bias_m": bias_m,
        "clock_bias_ns": bias_m / C * 1e9,
        "tc_sec": tc,
        "total_time_sec": t0_sec + tc,
        "residuals_m": dz.tolist(),
        "rms_m": rms,
        "PDOP": pdop,
        "n_ambiguities": n_arr.tolist(),
        "elevations_deg": topo_el.tolist(),
        "azimuths_deg": topo_az.tolist(),
        "n_satellites": nsat,
    }


################################################ STANDARD LS (≥4 sats, no CT) ################################################

def apvt_standard_ls(
    pseudoranges_m: np.ndarray,
    sat_positions: np.ndarray,
    initial_pos: Optional[np.ndarray] = None,
    max_iter: int = 10,
) -> Dict[str, Any]:
    """
    Classic 4-state LS PVT (X,Y,Z,b) for when full pseudoranges are available.
    """
    nsat = pseudoranges_m.shape[0]
    if nsat < 4:
        return {"success": False, "error": f"Need ≥4 sats, got {nsat}"}

    x = np.zeros(4)
    if initial_pos is not None:
        x[:3] = initial_pos

    for iteration in range(max_iter):
        ranges_pred = np.zeros(nsat)
        H = np.zeros((nsat, 4))
        for i in range(nsat):
            dx = sat_positions[:, i] - x[:3]
            r = np.linalg.norm(dx)
            ranges_pred[i] = r
            if r > 0:
                H[i, :3] = -dx / r
            H[i, 3] = 1.0
        res = pseudoranges_m - (ranges_pred + x[3])
        try:
            dX = np.linalg.lstsq(H, res, rcond=None)[0]
        except np.linalg.LinAlgError:
            return {"success": False, "error": "Singular matrix"}
        x += dX
        if np.linalg.norm(dX[:3]) < 1e-4:
            break

    ranges_final = np.zeros(nsat)
    for i in range(nsat):
        ranges_final[i] = np.linalg.norm(sat_positions[:, i] - x[:3])
    res_final = pseudoranges_m - (ranges_final + x[3])

    lat, lon, alt = cart2geo(x[0], x[1], x[2])
    try:
        Q = np.linalg.inv(H.T @ H)
        pdop = math.sqrt(Q[0, 0] + Q[1, 1] + Q[2, 2])
        hdop = math.sqrt(Q[0, 0] + Q[1, 1])
        vdop = math.sqrt(Q[2, 2])
        tdop = math.sqrt(Q[3, 3])
    except Exception:
        pdop = hdop = vdop = tdop = float("nan")

    return {
        "success": True,
        "llh": (lat, lon, alt),
        "xyz": x[:3].tolist(),
        "clock_bias_m": x[3],
        "clock_bias_ns": x[3] / C * 1e9,
        "residuals_m": res_final.tolist(),
        "rms_m": float(np.sqrt(np.mean(res_final ** 2))),
        "PDOP": pdop, "HDOP": hdop, "VDOP": vdop, "TDOP": tdop,
        "iterations": iteration + 1,
        "n_satellites": nsat,
    }


################################################ PIPELINE ################################################



def _run_pipeline_legacy(
    callback_status=None,
    callback_result=None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute the Snapshot PVT pipeline.

    Receives from the pipeline via kwargs:
        correlation_results : dict  – from SignalCorrelation (via ExecutionContext)
        ephemerides_data    : dict  – from EphemeridesManagement (via ExecutionContext)

    Computes:
        1. CT-Doppler (if ≥5 sats with Doppler) → coarse position + time
        2. CT-PRange  (if ≥5 sats with code phases) → final PVT
    Fallback to standard 4-state LS if only 4 sats.
    """
    def report_status(msg: str, progress: float = 0.0):
        _debug(msg)
        if callable(callback_status):
            callback_status(msg, progress)

    def report_result(key: str, value: Any):
        if callable(callback_result):
            callback_result(key, value)

    # ── Load configuration ──────────────────────────────────────────────
    config = load_config()
    use_auth_only = coerce_bool(config.get("use_authenticated_only", True))
    min_sats = coerce_int(config.get("min_satellites", 4))
    min_el = coerce_float(config.get("min_elevation_deg", 5.0))
    min_ppsp_db = coerce_float(config.get("min_ppsp_db", 3.0))
    min_cn0_dbhz = coerce_float(config.get("min_cn0_dbhz", 38.0))
    ref_lat = coerce_float(config.get("reference_latitude", 41.5007))
    ref_lon = coerce_float(config.get("reference_longitude", 2.1136))
    ref_alt = coerce_float(config.get("reference_altitude", 0.0))
    autonomous_mode = coerce_bool(config.get("autonomous_mode", False))

    report_status("Starting Snapshot PVT …", 0.05)

    # ── Gather inputs ───────────────────────────────────────────────────
    corr = kwargs.get("correlation_results", {})
    eph_data = kwargs.get("ephemerides_data", {})
    almanac_visible = kwargs.get("almanac_visible_satellites", [])

    # Log almanac visible satellites (sorted by elevation from Almanac block)
    if almanac_visible:
        _debug(f"Almanac visible satellites ({len(almanac_visible)}, sorted by elevation):")
        for sv in almanac_visible:
            _debug(f"  {sv['id']:>4s}  el={sv['elevation']:.1f}°  az={sv['azimuth']:.1f}°")
        report_result("almanac_visible_satellites",
                      [f"{sv['id']} (el={sv['elevation']:.0f}°)" for sv in almanac_visible])

    e1_results = corr.get("e1_results", [])
    fs_hz = corr.get("sample_rate_hz")
    ephemerides = eph_data.get("ephemerides", [])
    target_dt_iso = eph_data.get("target_datetime")

    if not e1_results:
        report_status("No E1B correlation results available", 1.0)
        report_result("pvt_status", "NO_MEASUREMENTS")
        return {"success": False, "error": "No E1B correlation results"}

    if not ephemerides:
        report_status("No ephemerides available", 1.0)
        report_result("pvt_status", "NO_EPHEMERIDES")
        return {"success": False, "error": "No ephemerides data"}

    if not fs_hz:
        fs_hz = 20e6  # default
        _debug(f"No sample_rate_hz in correlation_results, assuming {fs_hz/1e6:.0f} MHz")

    # ── Parse target time ───────────────────────────────────────────────
    if target_dt_iso:
        target_dt = datetime.fromisoformat(
            target_dt_iso.replace("Z", "+00:00") if isinstance(target_dt_iso, str) else target_dt_iso
        )
    else:
        target_dt = datetime.now(timezone.utc)
    _t = GalileoTime.from_datetime(target_dt)
    gal_sec = _t.gps_seconds_leap_corrected
    gal_tow = _t.gps_tow
    report_status(f"Target time: {target_dt.isoformat()}  (GAL TOW={gal_tow:.1f}s)", 0.1)
    report_result("target_datetime", target_dt.isoformat())

    # ── Extract per-satellite measurements ──────────────────────────────
    prn_list: List[int] = []
    code_phases_s: List[float] = []
    doppler_hz_list: List[float] = []
    cn0_list: List[float] = []

    # Build E6B Doppler lookup for cross-validation
    # Only trust E6B Doppler when the detection is reliable (PPSP ≥ min_ppsp_db).
    # Using a low threshold (e.g. 1.0 dB) causes false E6B Dopplers to reject
    # valid E1B detections via the cross-check.
    e6_results = corr.get("e6_results", [])
    _e6b_doppler: Dict[int, float] = {}
    for eres in e6_results:
        if "error" in eres:
            continue
        if eres.get("signal") != "E6B":
            continue
        eprn = eres.get("prn")
        eppsp = eres.get("ppsp_db", 0.0)
        if eprn is not None and eppsp >= min_ppsp_db:
            _e6b_doppler[eprn] = eres.get("best_doppler_hz", 0.0)

    rejected_prns: List[int] = []
    unhealthy_prns: List[int] = []
    doppler_xcheck_rejected: List[int] = []
    for res in e1_results:
        if "error" in res:
            continue
        prn = res.get("prn")
        if prn is None:
            continue

        # ── Health check: reject unhealthy satellites ──
        healthy_eph = [e for e in ephemerides
                       if e["prn"] == prn and e.get("sv_health", 1) == 0]
        if not healthy_eph:
            _debug(f"  PRN {prn} rejected: UNHEALTHY (no healthy ephemeris)")
            unhealthy_prns.append(prn)
            continue

        # Quality gate: reject weak detections
        ppsp = res.get("ppsp_db", 99.0)
        cn0 = res.get("cn0_dbhz", 0.0)
        if ppsp < min_ppsp_db:
            _debug(f"  PRN {prn} rejected: PPSP={ppsp:.1f} dB < {min_ppsp_db:.1f} dB")
            rejected_prns.append(prn)
            continue
        if cn0 < min_cn0_dbhz:
            _debug(f"  PRN {prn} rejected: C/N0={cn0:.1f} < {min_cn0_dbhz:.1f} dB-Hz")
            rejected_prns.append(prn)
            continue
        # Code phase in samples → seconds
        peak = res.get("peak_index_interp", res.get("peak_index"))
        if peak is None:
            continue
        cp_s = peak / fs_hz  # code delay [s]
        # Wrap to one code period (E1B = 4 ms)
        cp_s = cp_s % GAL_E1_CODE_PERIOD_S

        dopp = res.get("best_doppler_hz", 0.0)

        # ── E6B Doppler cross-validation ──
        # When a reliable E6B Doppler exists for this PRN, verify that the
        # E1B Doppler is consistent (scaled by the carrier frequency ratio).
        # A large discrepancy indicates a false E1B correlation lock.
        if prn in _e6b_doppler:
            e6b_dopp = _e6b_doppler[prn]
            expected_e1 = e6b_dopp * (GAL_E1_FREQ_HZ / GAL_E6_FREQ_HZ)
            dopp_err_hz = abs(dopp - expected_e1)
            # Tolerance: 500 Hz (covers fine-search quantisation + ionosphere)
            if dopp_err_hz > 500.0:
                _debug(f"  PRN {prn} rejected: E1B Doppler={dopp:.0f} Hz "
                       f"inconsistent with E6B={e6b_dopp:.0f} Hz "
                       f"(expected ~{expected_e1:.0f} Hz, err={dopp_err_hz:.0f} Hz)")
                doppler_xcheck_rejected.append(prn)
                continue

        prn_list.append(prn)
        code_phases_s.append(cp_s)
        doppler_hz_list.append(dopp)
        cn0_list.append(cn0)
    if rejected_prns:
        _debug(f"Rejected {len(rejected_prns)} PRNs (quality gate): {rejected_prns}")
    if doppler_xcheck_rejected:
        _debug(f"Rejected {len(doppler_xcheck_rejected)} PRNs (E6B Doppler cross-check): "
               f"{doppler_xcheck_rejected}")
    if unhealthy_prns:
        _debug(f"Rejected {len(unhealthy_prns)} PRNs (UNHEALTHY): {unhealthy_prns}")
        report_result("unhealthy_prns", [f"E{p:02d}" for p in unhealthy_prns])

    nsat = len(prn_list)
    report_result("satellites_used", nsat)
    report_result("prn_list", [f"E{p:02d}" for p in prn_list])
    _debug(f"Extracted {nsat} E1B measurements: PRNs {prn_list}")

    if nsat < min_sats:
        msg = f"Insufficient satellites: {nsat} < {min_sats}"
        report_status(msg, 1.0)
        report_result("pvt_status", "INSUFFICIENT_SATELLITES")
        return {"success": False, "error": msg, "satellites_used": nsat}

    # ── Verify ephemerides coverage ─────────────────────────────────────
    missing = [p for p in prn_list if not any(e["prn"] == p for e in ephemerides)]
    if missing:
        _debug(f"WARNING: no ephemerides for PRNs {missing}")

    code_phases_s_arr = np.array(code_phases_s)
    doppler_hz_arr = np.array(doppler_hz_list)

    # Always start the algorithm from (0,0,0) — true cold start.
    # The reference position (ref_lat/lon/alt) is used ONLY to compute
    # the final position error, never as algorithm seed.
    initial_llh = (0.0, 0.0, 0.0)
    cold_start = True

    # ── 1) CT-Doppler ──────────────────────────────────────────────────
    report_status("Running CT-Doppler …", 0.3)
    if nsat >= 5:
        if cold_start:
            _debug("Autonomous / cold-start mode: running grid-search CT-Doppler")
            report_status("Running cold-start CT-Doppler (grid search) …", 0.3)
            dop_result = ct_doppler_cold_start(
                doppler_hz_arr, prn_list, ephemerides,
                gal_sec,
                carrier_freq_hz=GAL_E1_FREQ_HZ,
                n_iter=30,
                grid_step_deg=30.0,
            )
            if dop_result and dop_result.get("success"):
                grid_init = dop_result.get("initial_llh_grid", (0, 0, 0))
                _debug(f"Grid search best init: lat={grid_init[0]:.0f}°, lon={grid_init[1]:.0f}°")
                report_result("cold_start_grid_init",
                              f"({grid_init[0]:.0f}°, {grid_init[1]:.0f}°)")
            elif dop_result is None:
                dop_result = {"success": False, "error": "Grid search returned no result"}
        else:
            dop_result = ct_doppler(
                doppler_hz_arr, prn_list, ephemerides,
                gal_sec, initial_llh,
                carrier_freq_hz=GAL_E1_FREQ_HZ,
            )
    else:
        # With <5 sats, CT-Doppler is under-determined; skip it
        _debug(f"Skipping CT-Doppler ({nsat} sats < 5); "
               f"using reference position as initial estimate")
        dop_result = {"success": False,
                      "error": f"Not enough sats for CT-Doppler ({nsat})"}

    if dop_result["success"]:
        _debug(f"CT-Doppler converged: LLH=({dop_result['llh'][0]:.5f}°, "
               f"{dop_result['llh'][1]:.5f}°, {dop_result['llh'][2]:.1f}m)  "
               f"tc={dop_result['tc_sec']:.3f}s  res={dop_result['residual_mps']:.2f}m/s")
        report_result("doppler_residual_mps", dop_result["residual_mps"])
        report_result("doppler_tc_sec", dop_result["tc_sec"])
        ct_llh = dop_result["llh"]
        ct_sec = dop_result["dopSec"]
    else:
        _debug(f"CT-Doppler did not converge (res={dop_result.get('residual_mps', '?')}). "
               f"Using (0,0,0) as initial estimate (cold start).")
        ct_llh = (0.0, 0.0, 0.0)
        ct_sec = gal_sec

    # ── 2) CT-PRange ───────────────────────────────────────────────────
    # Use altitude constraint when fewer than 5 satellites are available
    # In cold-start mode there is no reference altitude to constrain with
    alt_constraint = None
    if nsat < 5 and not cold_start:
        alt_constraint = ref_alt
    # Fix time when Doppler failed and we have few satellites,
    # reducing unknowns from 5 to 4 (enables 3-sat + altitude PVT)
    use_fix_time = (not dop_result["success"]) and nsat < 5
    if alt_constraint is not None:
        _debug(f"Altitude-constrained mode: h_ref={alt_constraint:.1f}m "
               f"({nsat} sats, fix_time={use_fix_time})")
    report_status("Running CT-PRange with code-period ambiguity resolution …", 0.5)
    pr_result = ct_prange(
        code_phases_s_arr, prn_list, ephemerides,
        ct_sec, ct_llh,
        code_period_ms=GAL_E1_CODE_PERIOD_MS,
        altitude_constraint_m=alt_constraint,
        fix_time=use_fix_time,
    )

    # ── Results ─────────────────────────────────────────────────────────
    gmaps_url = None
    if pr_result["success"]:
        lat, lon, alt = pr_result["llh"]
        report_status(
            f"PVT solution: {lat:.6f}°N  {lon:.6f}°E  {alt:.1f}m  "
            f"(RMS={pr_result['rms_m']:.1f}m, PDOP={pr_result['PDOP']:.1f})",
            0.9,
        )
        report_result("pvt_status", "SUCCESS")
        report_result("latitude_deg", lat)
        report_result("longitude_deg", lon)
        report_result("altitude_m", alt)
        report_result("clock_bias_ns", pr_result["clock_bias_ns"])
        report_result("PDOP", pr_result["PDOP"])
        report_result("rms_residual_m", pr_result["rms_m"])

        # Google Maps / OpenStreetMap links
        gmaps_url = f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
        osm_url = (f"https://www.openstreetmap.org/"
                   f"?mlat={lat:.6f}&mlon={lon:.6f}#map=15/{lat:.6f}/{lon:.6f}")
        report_result("google_maps_url", gmaps_url)
        report_result("openstreetmap_url", osm_url)
        _debug(f"Google Maps: {gmaps_url}")

        # Position error vs reference (if a reference is configured)
        if ref_lat != 0.0 or ref_lon != 0.0:
            ref_ecef = geod2cart((ref_lat, ref_lon, ref_alt))
            sol_ecef = np.array(pr_result["xyz"])
            err_m = np.linalg.norm(sol_ecef - ref_ecef)
            report_result("position_error_m", err_m)
            _debug(f"Position error vs reference: {err_m:.1f} m")
        else:
            _debug("No reference position configured for error computation")
    else:
        report_status(f"CT-PRange failed: {pr_result.get('error', pr_result.get('rms_m', '?'))}", 0.9)
        report_result("pvt_status", "FAILED")

    # ── Save JSON ───────────────────────────────────────────────────────
    results_dir = get_results_subdir("pvt")
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = results_dir / f"pvt_snapshot_{ts}.json"

    def _ser(obj):
        """JSON serialiser for numpy types."""
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return str(obj)

    with open(json_path, "w") as f:
        json.dump({
            "target_datetime": target_dt.isoformat(),
            "autonomous_mode": cold_start,
            "reference_llh": list(initial_llh) if not cold_start else None,
            "satellites": [f"E{p:02d}" for p in prn_list],
            "ct_doppler": {k: v for k, v in dop_result.items() if k != "success"},
            "ct_prange": {k: v for k, v in pr_result.items() if k != "success"},
            "pvt_success": pr_result["success"],
            "google_maps_url": gmaps_url if pr_result["success"] else None,
        }, f, indent=2, default=_ser)
    report_result("json_output", str(json_path))
    report_status("PVT computation complete", 1.0)

    return {
        "success": pr_result["success"],
        "ct_doppler": dop_result,
        "ct_prange": pr_result,
        "satellites_used": nsat,
        "prn_list": prn_list,
        "autonomous_mode": cold_start,
        "google_maps_url": gmaps_url,
        "json_output": str(json_path),
    }


############################################ PIPELINE EXECUTION (CONTRACT 1.0) ############################################

def run_pipeline(
    config=None,
    inputs=None,
    globals=None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: ephemerides_data, correlation_results, almanac_visible_satellites
    produces: pvt_results
    """
    inputs = dict(inputs or {})
    raw = _run_pipeline_legacy(
        correlation_results=inputs.get("correlation_results", {}),
        ephemerides_data=inputs.get("ephemerides_data", {}),
        almanac_visible_satellites=inputs.get("almanac_visible_satellites", []),
    )
    return {
        "outputs": {
            "pvt_results": raw,
        },
        "summary": {
            "success": raw.get("success") if isinstance(raw, dict) else False,
            "satellites_used": raw.get("satellites_used") if isinstance(raw, dict) else 0,
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"
