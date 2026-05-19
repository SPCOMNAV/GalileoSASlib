from __future__ import annotations

import csv
import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from utils.utils import (
    # Type coercion
    coerce_bool as _coerce_bool,
    coerce_int as _coerce_int,
    coerce_float as _coerce_float,
    coerce_string as _coerce_string,
    coerce_optional_string as _coerce_optional_string,
    coerce_path,
    # JSON helpers
    load_json_file,
    save_json_file,
    load_schema as _load_schema_util,
    load_config as _load_config_util,
    save_config as _save_config_util,
    # Schema utilities
    extract_defaults_from_schema,
    # Serialization
    serialise_value as _serialise_value_util,
    # Logging
    console_log,
    # Paths
    BASE_DIR,
    CONFIG_DIR as CONFIG_JSON_DIR,
    SCHEMA_DIR as DEFAULT_JSON_DIR,
    DATA_DIR,
    RESULTS_DIR,
    get_results_subdir,
    # GST utilities
    GST_EPOCH,
    GST_WEEK_SECONDS,
    GST_WN_MODULO,
    datetime_to_gst_seconds,
    gst_calendar_to_gst_seconds,
    gst_to_week_tow,
)
from utils.time_galileo import GalileoTime

# ---------------------------------------------------------------------------
# AES backends (pycryptodome o cryptography)
# ---------------------------------------------------------------------------

try:
    from Crypto.Cipher import AES
    _AES = "pycryptodome"
except Exception:
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        _AES = "cryptography"
    except Exception:
        _AES = None


# ---------------------------------------------------------------------------
# Constantes y paths
# ---------------------------------------------------------------------------

DEFAULT_ECS_OUTPUT_DIR = RESULTS_DIR / "ECS"

MODULE_FILE = "RECSDecryption"

# Regex para validar claves TESLA (32 caracteres hex)
HEX32_RE = re.compile(r"^[0-9A-Fa-f]{32}$")

# Aliases de constantes GST (legacy compatibility)
WEEK_SEC = GST_WEEK_SECONDS
WN_MOD = GST_WN_MODULO


# ---------------------------------------------------------------------------
# Block-specific coercion utilities
# ---------------------------------------------------------------------------

def _coerce_epoch(value):
    """Convert a value to epoch (int) or None."""
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _coerce_preview_length(value, default):
    """Limit preview length."""
    parsed = _coerce_int(value, default, minimum=1)
    return min(512, parsed)


def _normalise_hex(text):
    """Normalize a hexadecimal string."""
    if text is None:
        return None
    cleaned = str(text).strip().replace(" ", "").lower()
    return cleaned.upper() if cleaned else None


# ---------------------------------------------------------------------------
# Utilidades de path y archivos
# ---------------------------------------------------------------------------

def _resolve_path(text, base_dir):
    """Resuelve un path relativo o absoluto usando coerce_path de utils."""
    path = coerce_path(text)
    if path is None:
        return None
    path = path.expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _ensure_output_dir(path):
    """Crea el directorio si no existe."""
    Path(path).mkdir(parents=True, exist_ok=True)


def _bytes_to_dec_string(b):
    """Convierte bytes a string decimal separado por espacios."""
    return " ".join(str(x) for x in b)


def _to_utc(dt):
    """Convierte datetime a UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Configuration loading from JSON
# ---------------------------------------------------------------------------

def load_schema():
    """Load the block schema (wrapper around utils.load_schema)."""
    schema = _load_schema_util(MODULE_FILE)
    if not schema:
        schema_path = DEFAULT_JSON_DIR / f"{MODULE_FILE}_schema.json"
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    return schema


def load_defaults():
    """Extrae los valores default del schema (usando utils.extract_defaults_from_schema)."""
    schema = load_schema()
    return extract_defaults_from_schema(schema)


def load_config():
    """Load current configuration (merged with defaults)."""
    defaults = load_defaults()
    current = _load_config_util(MODULE_FILE)
    if current:
        defaults.update(current)
    return defaults


def save_config(config):
    """Save current configuration (wrapper around utils.save_config)."""
    _save_config_util(MODULE_FILE, config)
    return CONFIG_JSON_DIR / f"{MODULE_FILE}.json"


# ---------------------------------------------------------------------------
# Parser de header y filename RECS
# ---------------------------------------------------------------------------

def parseRecsHeader(hdr):
    """Parsea el header de 16 bytes de un archivo RECS."""
    if len(hdr) != 16:
        raise ValueError("Header must be 16 bytes")
    bits = f"{int.from_bytes(hdr, 'big'):0128b}"
    i = 0
    
    def take(n):
        nonlocal i
        s = bits[i:i + n]
        i += n
        return int(s, 2)
    
    provider = hdr[0:4].decode("ascii", errors="replace")
    i = 32
    intv = take(8)
    _res = take(4)
    t0_wn = take(12)
    t0_atow = take(24)
    svid = take(8)
    kdi = take(8)
    rand_flag = take(8)
    file_version = take(8)
    header_len = take(16)
    
    return {
        "provider": provider,
        "intv": intv,
        "t0_wn": t0_wn,
        "t0_atow": t0_atow,
        "svid": svid,
        "kdi": kdi,
        "rand": rand_flag,
        "file_version": file_version,
        "header_len": header_len,
    }


def _tstart_to_datetime(yy, ddd, hh, mm, dss):
    """Convierte campos de tiempo a datetime."""
    year = 2000 + yy
    base = datetime(year, 1, 1) + timedelta(days=ddd - 1)
    seconds = hh * 3600 + mm * 60 + (dss / 10.0)
    return base + timedelta(seconds=seconds)


def parseRecsFilename(name):
    """Parsea el nombre de archivo RECS para extraer metadatos."""
    fname = Path(name).name
    pat = re.compile(
        r"^([A-Za-z0-9]{6})_(\d{12})_(\d{2})_(\d{1,3})_([01])_(\d{2})\.RCS$",
        re.IGNORECASE,
    )
    m = pat.match(fname)
    if not m:
        raise ValueError(f"Nombre de archivo RCS no cumple el formato esperado: {fname}")
    
    provider_and_iv = m.group(1)
    tstart_str = m.group(2)
    svid_str = m.group(3)
    kdi_str = m.group(4)
    rand_str = m.group(5)
    filever_str = m.group(6)
    
    provider = provider_and_iv[:4]
    interface_version = provider_and_iv[4:]
    
    yy = int(tstart_str[0:2])
    ddd = int(tstart_str[2:5])
    hh = int(tstart_str[5:7])
    mm = int(tstart_str[7:9])
    sss = int(tstart_str[9:12])
    
    tstart_dt = _tstart_to_datetime(yy, ddd, hh, mm, sss)
    
    return {
        "provider": provider,
        "interface_version": interface_version,
        "yy": yy,
        "ddd": ddd,
        "hh": hh,
        "mm": mm,
        "dss": sss,
        "tstart_dt": tstart_dt,
        "svid": int(svid_str),
        "kdi": int(kdi_str),
        "rand": int(rand_str),
        "file_version": int(filever_str),
    }


def find_recs_for_prn(recs_directory, prn, gst_target=None):
    """
    Find RECS file for a specific PRN in a directory.
    
    Args:
        recs_directory: Path to directory containing RECS files
        prn: PRN/SVID to search for
        gst_target: Optional GST timestamp to match (for time-specific search)
    
    Returns:
        Path to RECS file or None if not found
    
    Filename format: XXXXII_YYDDDHHMMSSS_ss_K_R_VV.RCS
    Where ss is the SVID (PRN)
    """
    recs_dir = Path(recs_directory)
    if not recs_dir.exists():
        return None
    
    prn_int = int(prn)
    prn_str = f"{prn_int:02d}"  # Pad to 2 digits
    
    # Find all RECS files matching this PRN
    matching_files = []
    for rcs_file in recs_dir.glob("*.RCS"):
        try:
            info = parseRecsFilename(rcs_file.name)
            if info["svid"] == prn_int:
                matching_files.append((rcs_file, info))
        except ValueError:
            continue  # Skip files that don't match the pattern
    
    if not matching_files:
        return None
    
    # If gst_target specified, find the closest match
    if gst_target is not None:
        best_match = None
        best_diff = float("inf")
        for rcs_file, info in matching_files:
            gst_file = gst_calendar_to_gst_seconds(_to_utc(info["tstart_dt"]))
            diff = abs(gst_file - gst_target)
            if diff < best_diff:
                best_diff = diff
                best_match = rcs_file
        return best_match
    
    # Otherwise return the most recent file
    matching_files.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return matching_files[0][0]


def decrypt_recs_for_prn(recs_directory, prn, tesla_keys_path, gst_target=None, include_padding=False):
    """
    Find and decrypt RECS for a specific PRN.
    
    Args:
        recs_directory: Path to directory containing RECS files
        prn: PRN/SVID to decrypt
        tesla_keys_path: Path to TESLA keys file (XML, CSV, or JSON)
        gst_target: Optional GST timestamp for time-specific search
        include_padding: Whether to include padding in ECS
    
    Returns:
        Dict with decryption results including ECS hex, or None if RECS not found
    """
    # Find RECS file for this PRN
    recs_file = find_recs_for_prn(recs_directory, prn, gst_target)
    if recs_file is None:
        return None
    
    # Build config for decryption - detect file type by extension
    config = {
        "recs_path": str(recs_file),
        "include_padding": include_padding,
        "save_ecs": True,
    }
    
    if tesla_keys_path:
        keys_path = Path(tesla_keys_path)
        suffix = keys_path.suffix.lower()
        if suffix == ".json":
            config["tesla_json_path"] = str(tesla_keys_path)
        else:  # .xml, .csv, or other
            config["manual_xml_path"] = str(tesla_keys_path)
    
    try:
        result = run_pipeline(config_overrides=config)
        result["prn"] = int(prn)
        result["recs_file_used"] = str(recs_file)
        return result
    except Exception as e:
        return {
            "prn": int(prn),
            "recs_file_used": str(recs_file),
            "error": str(e),
        }


def decrypt_recs_for_prn_with_kdi(recs_directory, prn, tesla_keys_path, recs_kdi=None, gst_target=None, include_padding=False):
    """
    Find and decrypt RECS for a specific PRN with optional KDI filter.
    
    Args:
        recs_directory: Path to directory containing RECS files
        prn: PRN/SVID to decrypt
        tesla_keys_path: Path to TESLA keys file (XML, CSV, or JSON)
        recs_kdi: Key Delay Indicator filter (0=current key, 1=next key). None means no filter.
        gst_target: Optional GST timestamp for time-specific search
        include_padding: Whether to include padding in ECS
    
    Returns:
        Dict with decryption results including ECS hex, or None if RECS not found
    """
    recs_dir = Path(recs_directory)
    if not recs_dir.exists():
        return None
    
    prn_int = int(prn)
    
    # Find all RECS files matching this PRN
    matching_files = []
    for rcs_file in recs_dir.glob("*.RCS"):
        try:
            info = parseRecsFilename(rcs_file.name)
            if info["svid"] == prn_int:
                # Filter by KDI if specified
                if recs_kdi is not None and info.get("kdi") != recs_kdi:
                    continue
                matching_files.append((rcs_file, info))
        except ValueError:
            continue
    
    if not matching_files:
        _debug(f"No RECS found for PRN {prn}" + (f" with KDI={recs_kdi}" if recs_kdi is not None else ""))
        return None
    
    # If gst_target specified, find the closest match
    if gst_target is not None:
        best_match = None
        best_diff = float("inf")
        for rcs_file, info in matching_files:
            gst_file = gst_calendar_to_gst_seconds(_to_utc(info["tstart_dt"]))
            diff = abs(gst_file - gst_target)
            if diff < best_diff:
                best_diff = diff
                best_match = rcs_file
        recs_file = best_match
    else:
        # Return the most recent file
        matching_files.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
        recs_file = matching_files[0][0]
    
    if recs_kdi is not None:
        _debug(f"Selected RECS for PRN {prn} with KDI={recs_kdi}: {recs_file.name}")
    
    # Build config for decryption
    config = {
        "recs_path": str(recs_file),
        "include_padding": include_padding,
        "save_ecs": True,
    }
    
    if tesla_keys_path:
        keys_path = Path(tesla_keys_path)
        suffix = keys_path.suffix.lower()
        if suffix == ".json":
            config["tesla_json_path"] = str(tesla_keys_path)
        else:
            config["manual_xml_path"] = str(tesla_keys_path)
    
    try:
        result = run_pipeline(config_overrides=config)
        result["prn"] = int(prn)
        result["recs_file_used"] = str(recs_file)
        return result
    except Exception as e:
        return {
            "prn": int(prn),
            "recs_file_used": str(recs_file),
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Cryptographic functions
# ---------------------------------------------------------------------------



def packWnTowBe(wn, tow):
    """Pack WN/TOW in big-endian format."""
    if not (0 <= wn < (1 << 12)):
        raise ValueError("WN out of range (12 bits)")
    if not (0 <= tow < (1 << 20)):
        raise ValueError("TOW out of range (20 bits)")
    val = (wn << 20) | tow
    return struct.pack(">I", val)


def calculateP(gst_sf_seconds, rand_flag):
    """Calculate P vector for key derivation."""
    wn, tow = GalileoTime(gst_sf_seconds).week_tow_12bit
    gst_wn_tow_be = packWnTowBe(wn, tow)
    return gst_wn_tow_be + bytes([rand_flag & 0xFF]) + b"\x00" * 11


def calculateIV(tesla_key_hex, P):
    """Calcula la clave AES derivada y el IV."""
    K_R = hashlib.sha256(bytes.fromhex(tesla_key_hex)).digest()
    IV = hashlib.sha256(P).digest()[:16]
    return K_R, IV


def aesCBCDecrypt(ct, key, iv):
    """Descifra usando AES-CBC."""
    if _AES is None:
        raise RuntimeError("No AES backend found. Install 'pycryptodome' or 'cryptography'.")
    if len(ct) % 16 != 0:
        raise ValueError("Ciphertext length must be a multiple of 16 bytes.")
    if _AES == "pycryptodome":
        return AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    dec = cipher.decryptor()
    return dec.update(ct) + dec.finalize()


def decryptRECSFromFile(rcs_path, tesla_key_hex, gst_for_p, padding):
    """Decrypt a complete RECS file.
    
    Args:
        rcs_path: Path to the .RCS file.
        tesla_key_hex: 32-char hex TESLA key.
        gst_for_p: GST seconds for the P vector (subframe boundary, NOT
                    the key's own stored epoch).
        padding: If False, strip last 10 bytes of decrypted output.
    """
    raw = Path(rcs_path).read_bytes()
    header, body = raw[:16], raw[16:]
    hdr_info = parseRecsHeader(header)
    fname_info = parseRecsFilename(Path(rcs_path).name)
    P = calculateP(gst_for_p, hdr_info["rand"])
    K_R, IV = calculateIV(tesla_key_hex, P)
    decrypted = aesCBCDecrypt(body, K_R, IV)
    if not padding:
        decrypted = decrypted[:len(decrypted) - 10]
    return body.hex().upper(), IV.hex().upper(), decrypted.hex().upper(), list(P), fname_info


# ---------------------------------------------------------------------------
# Carga de claves TESLA
# ---------------------------------------------------------------------------

def parse_xml(xml_text):
    """Parsea claves TESLA desde XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc
    
    if root.tag != "OsnmaKeys":
        raise ValueError(f"Unexpected root tag: '{root.tag}' (expected 'OsnmaKeys')")
    
    out = {}
    for key_node in root.findall("key"):
        key_elem = key_node.find("osnma_key")
        epoch_elem = key_node.find("epoch")
        if key_elem is None or epoch_elem is None:
            raise ValueError("Incomplete <key> element: requires <osnma_key> and <epoch>")
        key_str = (key_elem.text or "").strip()
        epoch_str = (epoch_elem.text or "").strip()
        if not HEX32_RE.match(key_str):
            raise ValueError(f"Invalid OSNMA key (must be 32 hex chars): '{key_str}'")
        epoch_val = int(epoch_str)
        out[epoch_val] = key_str.upper()
    return out


def parse_csv(csv_text):
    """Parsea claves TESLA desde CSV."""
    text = csv_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return {}
    
    reader = csv.DictReader(StringIO(text))
    rows = list(reader) if reader.fieldnames else []
    
    if rows and reader.fieldnames:
        cols = [c.strip().lower() for c in reader.fieldnames]
        
        def pick(names):
            for n in names:
                if n in cols:
                    return reader.fieldnames[cols.index(n)]
            return None
        
        key_col = pick(["osnma_key", "teslakeyhex", "tesla_key", "key"])
        epoch_col = pick(["epoch", "teslakeygst", "gst"])
        
        if key_col and epoch_col:
            out = {}
            for r in rows:
                k = str(r.get(key_col, "")).strip()
                e = str(r.get(epoch_col, "")).strip()
                if k and HEX32_RE.fullmatch(k):
                    try:
                        out[int(e)] = k.upper()
                    except:
                        pass
            if out:
                return out
    
    # Fallback sin cabeceras
    out = {}
    rdr = csv.reader(StringIO(text))
    for row in rdr:
        if len(row) < 2:
            continue
        k = row[0].strip()
        e = row[1].strip()
        if HEX32_RE.fullmatch(k):
            try:
                out[int(e)] = k.upper()
            except:
                pass
    return out


def _load_tesla_keys_from_json(path):
    """Carga claves TESLA desde JSON."""
    data = load_json_file(path)
    result = {}
    
    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        tmp = []
        for entry in data:
            if isinstance(entry, dict) and "epoch" in entry and "osnma_key" in entry:
                tmp.append((entry["epoch"], entry["osnma_key"]))
        items = tmp
    else:
        items = []
    
    for entry in items:
        epoch, value = entry
        try:
            epoch_int = int(epoch)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, str):
            continue
        key = value.strip().upper()
        if HEX32_RE.fullmatch(key):
            result[epoch_int] = key
    
    return result


def _apply_epoch_shift(keys, shift_sec):
    """Apply a shift to all epochs."""
    if not shift_sec:
        return keys
    return {e + shift_sec: k for e, k in keys.items()}


def _load_tesla_keys(config, base_dir, tesla_keys_param=None):
    """Load TESLA keys according to configuration or from parameter."""
    # NOTE: epoch_shift is no longer applied by default.  The correct key
    # selection (one subframe before gst_sf) is now handled in run_pipeline.
    # A non-zero shift can still be forced via config for special cases.
    epoch_shift = int(config.get("tesla_epoch_shift", 0))
    
    # Option 1: Explicit manual_xml_path
    manual_path_text = _coerce_optional_string(config.get("manual_xml_path"), None)
    if manual_path_text:
        p = _resolve_path(manual_path_text, base_dir)
        if p is None or not p.exists():
            raise FileNotFoundError(f"Manual TESLA file not found at {manual_path_text}")
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".csv":
            keys = parse_csv(text)
        else:
            keys = parse_xml(text)
        return _apply_epoch_shift(keys, epoch_shift)
    
    # Option 2: tesla_keys_path (from ConfigInit propagation)
    tesla_keys_path_text = _coerce_optional_string(config.get("tesla_keys_path"), None)
    if tesla_keys_path_text:
        p = _resolve_path(tesla_keys_path_text, base_dir)
        if p and p.exists():
            text = p.read_text(encoding="utf-8")
            if p.suffix.lower() == ".csv":
                keys = parse_csv(text)
            elif p.suffix.lower() == ".xml":
                keys = parse_xml(text)
            else:
                keys = _load_tesla_keys_from_json(p)
            console_log("RECSDecryption", f"Loaded TESLA keys from: {p}")
            return _apply_epoch_shift(keys, epoch_shift)
    
    # Option 3: tesla_json_path (legacy)
    defaults = load_defaults()
    cand = _coerce_optional_string(config.get("tesla_json_path"), defaults.get("tesla_json_path"))
    p = _resolve_path(cand, base_dir) if cand else None
    if p and p.exists():
        if p.suffix.lower() == ".csv":
            keys = parse_csv(p.read_text(encoding="utf-8"))
        else:
            keys = _load_tesla_keys_from_json(p)
        return _apply_epoch_shift(keys, epoch_shift)
    
    # Option 4: Keys from parameter (from SASpipeline)
    if tesla_keys_param:
        console_log("RECSDecryption", f"Using {len(tesla_keys_param)} TESLA keys from pipeline")
        return _apply_epoch_shift(tesla_keys_param, epoch_shift)
    
    return {}


# ---------------------------------------------------------------------------
# Key search
# ---------------------------------------------------------------------------

def _sorted_items(d):
    """Sort a dictionary by key."""
    return sorted(d.items(), key=lambda x: x[0])


def find_key_at(keys, t):
    """Find valid key for a given epoch (before or equal)."""
    items = _sorted_items(keys)
    lo, hi = 0, len(items) - 1
    ans = None
    while lo <= hi:
        mid = (lo + hi) // 2
        e_mid, k_mid = items[mid]
        if e_mid <= t:
            ans = (e_mid, k_mid)
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def find_next_key(keys, epoch):
    """Find the next key after an epoch."""
    for e, k in _sorted_items(keys):
        if e > epoch:
            return e, k
    return None


def _find_key_for_epoch(keys, target_epoch, use_next_key=False):
    """
    Find the TESLA key valid for a given epoch.
    Returns (epoch, key) tuple or None if not found.
    
    Args:
        keys: Dict of epoch -> key
        target_epoch: Target GST epoch in seconds
        use_next_key: If True (KDI=1), use the next key instead of current
    
    The valid key is the one with the largest epoch <= target_epoch.
    If use_next_key is True, returns the next key after target_epoch.
    """
    if use_next_key:
        return find_next_key(keys, target_epoch)
    return find_key_at(keys, target_epoch)


# ---------------------------------------------------------------------------
# Result serialization
# ---------------------------------------------------------------------------

def _serialise_value(value):
    """Serialize a value for JSON (wrapper around utils.serialise_value)."""
    return _serialise_value_util(value)


def _format_preview(text, size):
    """Formatea un preview truncado."""
    if size <= 0 or size >= len(text):
        return text
    return text[:size] + "..."


def _ecs_to_polar_samples(hex_text, limit):
    """Convierte ECS hex a muestras polares."""
    raw = bytes.fromhex(hex_text)
    bits = list(
        (1 if bit == 0 else -1)
        for byte in raw
        for bit in [(byte >> shift) & 1 for shift in range(7, -1, -1)]
    )
    if limit > 0:
        bits = bits[:limit]
    return bits


def _match_prn(filepath, prns):
    """Check if a RECS file matches any of the specified PRNs."""
    try:
        p = Path(filepath) if isinstance(filepath, str) else filepath
        parts = p.stem.split("_")
        if len(parts) >= 3:
            file_prn = int(parts[2])
            return file_prn in prns
    except (ValueError, IndexError):
        pass
    return False


def _match_kdi(filepath, kdi):
    """Check if a RECS file matches the specified KDI."""
    try:
        p = Path(filepath) if isinstance(filepath, str) else filepath
        parts = p.stem.split("_")
        if len(parts) >= 4:
            file_kdi = int(parts[3])
            return file_kdi == kdi
    except (ValueError, IndexError):
        pass
    return False


def _file_epoch_gst(filepath):
    """Return the GST seconds encoded in a RECS unitary filename, or None."""
    try:
        p = Path(filepath) if isinstance(filepath, str) else filepath
        info = parseRecsFilename(p.name)
        return gst_calendar_to_gst_seconds(_to_utc(info["tstart_dt"]))
    except Exception:
        return None


def _pick_recs_for_epoch(files, target_epoch):
    """Return the file whose filename-encoded GST is closest to ``target_epoch``.
    Falls back to the first file if no epoch can be parsed."""
    if target_epoch is None:
        return files[0]
    best = None
    best_dt = None
    for f in files:
        gst = _file_epoch_gst(f)
        if gst is None:
            continue
        dt = abs(gst - float(target_epoch))
        if best is None or dt < best_dt:
            best, best_dt = f, dt
    return best if best is not None else files[0]


# ---------------------------------------------------------------------------
# PIPELINE PRINCIPAL
# ---------------------------------------------------------------------------

def _run_pipeline_legacy(
    config_overrides=None,
    dry_run=False,
    *,
    recs_enabled: bool = True,
    recs_files: Optional[List[str]] = None,
    tesla_keys: Optional[Dict[int, str]] = None,
    **kwargs,
):
    """
    Execute the RECS decryption pipeline.
    
    Args:
        config_overrides: Dict with values that override configuration
        dry_run: If True, don't save files to disk
        recs_enabled: Whether RECS decryption is enabled (from SASpipeline).
                      If False, returns early with skip message.
        recs_files: List of RECS file paths (from SASpipeline via ExecutionContext).
        tesla_keys: Dict of TESLA keys {epoch: key_hex} (from SASpipeline via ExecutionContext).
    
    Returns:
        Dict with decryption results
    """
    # Check if RECS processing is enabled
    if not recs_enabled:
        console_log("RECSDecryption", "RECS decryption disabled (no encrypted SVIDs)")
        return {
            "skipped": True,
            "reason": "RECS decryption disabled (no encrypted SVIDs)",
        }
    
    # Load configuration
    config = load_config()
    if config_overrides:
        config.update(config_overrides)
    
    defaults = load_defaults()
    
    # Resolver path del archivo RECS - try parameter first, then explicit path, then directory
    recs_path_text = _coerce_string(config.get("recs_path"), "")
    recs_directory_text = _coerce_string(config.get("recs_directory"), defaults.get("recs_directory", ""))
    
    recs_path = None
    
    # Option 0: Get RECS files from parameter (from SASpipeline)
    if recs_files:
        ctx_recs_files = list(recs_files)  # Copy to avoid mutation
        # Filter by PRN if specified
        prn_encrypted = _coerce_string(config.get("prn_encrypted_list"), "")
        if prn_encrypted:
            prns = [int(p.strip()) for p in prn_encrypted.split(",") if p.strip().isdigit()]
            if prns:
                filtered = [f for f in ctx_recs_files if _match_prn(f, prns)]
                if filtered:
                    ctx_recs_files = filtered
        
        # Filter by KDI if specified
        recs_kdi = config.get("recs_kdi")
        if recs_kdi is not None:
            kdi_filtered = [f for f in ctx_recs_files if _match_kdi(f, recs_kdi)]
            if kdi_filtered:
                ctx_recs_files = kdi_filtered
        
        if ctx_recs_files:
            target_epoch_cfg = _coerce_epoch(config.get("target_epoch_override"))
            picked = _pick_recs_for_epoch(ctx_recs_files, target_epoch_cfg)
            recs_path = Path(picked) if isinstance(picked, str) else picked
            if target_epoch_cfg is not None:
                console_log(
                    "RECSDecryption",
                    f"Selected RECS file for target_epoch={int(target_epoch_cfg)}: {recs_path.name}"
                )
            else:
                console_log("RECSDecryption", f"Using RECS file: {recs_path.name}")
    
    # Option 1: Explicit recs_path provided
    if not recs_path and recs_path_text:
        recs_path = _resolve_path(recs_path_text, BASE_DIR) or Path(recs_path_text).resolve()
        if not recs_path.exists():
            raise FileNotFoundError(f"RECS file not found at {recs_path}")
    
    # Option 2: Search in recs_directory
    elif recs_directory_text:
        recs_dir = _resolve_path(recs_directory_text, BASE_DIR) or Path(recs_directory_text).resolve()
        if not recs_dir.exists():
            raise FileNotFoundError(f"RECS directory not found: {recs_dir}")
        
        # Find RECS files in directory
        recs_files = list(recs_dir.glob("*.RCS")) + list(recs_dir.glob("*.rcs"))
        if not recs_files:
            raise FileNotFoundError(f"No RECS files found in {recs_dir}")
        
        # If prn_encrypted_list is provided, filter by PRN
        prn_encrypted = _coerce_string(config.get("prn_encrypted_list"), "")
        if prn_encrypted:
            prns = [int(p.strip()) for p in prn_encrypted.split(",") if p.strip().isdigit()]
            if prns:
                # Filter files by PRN (format: GSCX01_EPOCH_PRN_KDI_...)
                filtered = []
                for f in recs_files:
                    try:
                        parts = f.stem.split("_")
                        if len(parts) >= 3:
                            file_prn = int(parts[2])
                            if file_prn in prns:
                                filtered.append(f)
                    except (ValueError, IndexError):
                        pass
                if filtered:
                    recs_files = filtered
        
        # Filter by KDI if specified (format: GSCX01_EPOCH_PRN_KDI_RAND_VER)
        recs_kdi = config.get("recs_kdi")
        if recs_kdi is not None:
            kdi_filtered = []
            for f in recs_files:
                try:
                    parts = f.stem.split("_")
                    if len(parts) >= 4:
                        file_kdi = int(parts[3])
                        if file_kdi == recs_kdi:
                            kdi_filtered.append(f)
                except (ValueError, IndexError):
                    pass
            if kdi_filtered:
                recs_files = kdi_filtered
                console_log("RECSDecryption", f"Filtered RECS files by KDI={recs_kdi}")
        
        # Pick the file whose filename-encoded epoch is closest to
        # target_epoch_override (falls back to the first when there is no hint).
        target_epoch_cfg = _coerce_epoch(config.get("target_epoch_override"))
        recs_path = _pick_recs_for_epoch(recs_files, target_epoch_cfg)
        if target_epoch_cfg is not None:
            console_log(
                "RECSDecryption",
                f"Selected RECS file for target_epoch={int(target_epoch_cfg)}: {recs_path.name}"
            )
        else:
            console_log("RECSDecryption", f"Auto-detected RECS file: {recs_path}")
    
    else:
        # Fallback to default - but only if a valid path exists
        recs_path_text = defaults.get("recs_path", "")
        if recs_path_text:
            recs_path = _resolve_path(recs_path_text, BASE_DIR) or Path(recs_path_text).resolve()
            if not recs_path.exists():
                recs_path = None
    
    # If no RECS file found, skip decryption
    if recs_path is None:
        console_log("RECSDecryption", "No RECS files available for decryption")
        return {
            "skipped": True,
            "reason": "No RECS files available (downloader did not find any matching files)",
        }
    
    # Configuration parameters
    include_padding = _coerce_bool(config.get("include_padding"), False)
    preview_hex_chars = _coerce_preview_length(config.get("preview_hex_chars"), defaults.get("preview_hex_chars", 40))
    preview_polar_samples = _coerce_preview_length(config.get("preview_polar_samples"), defaults.get("preview_polar_samples", 32))
    
    # Load TESLA keys (from parameter or config files)
    keys = _load_tesla_keys(config, BASE_DIR, tesla_keys_param=tesla_keys)
    
    # Parse filename to get epoch and KDI
    fname_info = parseRecsFilename(recs_path.name)
    gst_from_filename = gst_calendar_to_gst_seconds(_to_utc(fname_info["tstart_dt"]))
    
    # KDI (Key Delay Indicator): determines which 30s subframe boundary to use
    #   KDI=0 → subframe boundary at or before RECS epoch
    #   KDI=1 → subframe boundary after RECS epoch
    kdi = fname_info.get("kdi", 0)
    
    # Determine target epoch (allow override from config)
    target_epoch = _coerce_epoch(config.get("target_epoch_override")) or gst_from_filename
    
    # ── Subframe-aligned GST for P vector ──────────────────────────────
    # The P vector must use the GST of the 30s subframe boundary, not
    # the RECS unitary epoch directly.
    SUBFRAME_DUR = 30
    gst_sf_base = int(target_epoch // SUBFRAME_DUR) * SUBFRAME_DUR
    if kdi == 1:
        gst_sf = gst_sf_base + SUBFRAME_DUR
    else:
        gst_sf = gst_sf_base
    
    # Find the TESLA key at gst_sf.  The SAS server stores TESLA keys
    # indexed by their *validity* epoch (the subframe they were generated
    # for), NOT the disclosure epoch.  So the key for subframe gst_sf is
    # stored at epoch == gst_sf.
    key_result = find_key_at(keys, gst_sf)
    
    if key_result is None:
        error_msg = (
            f"No TESLA key found for gst_sf={gst_sf} (need key at epoch <= {gst_sf}). "
            f"KDI={kdi}, Available keys: {len(keys)}"
        )
        console_log("RECSDecryption", f"ERROR: {error_msg}")
        
        if keys:
            key_epochs = sorted(keys.keys())
            console_log("RECSDecryption", f"Available key epochs: {key_epochs[:5]}...{key_epochs[-5:] if len(key_epochs) > 5 else ''}")
        
        return {
            "error": True,
            "error_message": error_msg,
            "recs_path": str(recs_path),
            "target_epoch": target_epoch,
            "gst_sf": gst_sf,
            "kdi": kdi,
            "available_keys": len(keys),
            "skipped": False,
        }
    
    selected_epoch, selected_key = key_result
    console_log("RECSDecryption",
                f"KDI={kdi} | gst_sf={gst_sf} | TESLA key epoch={selected_epoch} "
                f"(target: {target_epoch})")
    
    metadata = {
        "target_epoch": target_epoch,
        "gst_from_filename": gst_from_filename,
        "gst_sf": gst_sf,
        "fname_info": fname_info,
        "kdi": kdi,
        "selected_epoch": selected_epoch,
        "selected_osnma_key": selected_key,
        "available_keys": len(keys),
    }
    
    # Decrypt RECS — P vector uses gst_sf (subframe boundary), not the
    # key's own stored epoch.
    try:
        recs_body_hex, iv_hex, ecs_hex, P_vector, header_info = decryptRECSFromFile(
            str(recs_path),
            selected_key,
            gst_sf,
            include_padding,
        )
    except Exception as exc:
        raise RuntimeError(
            f"RECS decryption failed: key epoch {selected_epoch}, gst_sf {gst_sf}: {exc}"
        ) from exc
    
    # Calcular claves derivadas
    aes_key_bytes, _ = calculateIV(selected_key, bytes(P_vector))
    aes_key_hex_str = aes_key_bytes.hex().upper()
    P_hex = bytes(P_vector).hex().upper()
    
    # Representaciones decimales
    P_dec_string = " ".join(str(x) for x in P_vector)
    aes_key_dec_string = " ".join(str(x) for x in aes_key_bytes)
    iv_dec_string = " ".join(str(x) for x in bytes.fromhex(iv_hex))
    
    # Guardar ECS si se requiere
    # If output_dir is empty, use execution results directory
    output_dir_text = _coerce_string(config.get("output_dir"), "")
    if output_dir_text:
        output_dir = _resolve_path(output_dir_text, BASE_DIR)
    else:
        # Use execution-specific results directory
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        if ctx.get_execution_id():
            output_dir = ctx.get_execution_results_dir("ECS")
        else:
            # Fallback for standalone execution
            output_dir = get_results_subdir("ECS")
    output_basename = _coerce_string(config.get("output_basename"), defaults.get("output_basename", "ecs_decrypted"))
    
    output_path = None
    if not dry_run and _coerce_bool(config.get("save_ecs"), True):
        _ensure_output_dir(output_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = output_dir / f"{output_basename}_{timestamp}.bin"
        output_path.write_bytes(bytes.fromhex(ecs_hex))
    
    # Actualizar metadata
    metadata.update({
        "header": header_info,
        "recs_body_preview": _format_preview(recs_body_hex, preview_hex_chars),
        "ecs_hex_full": ecs_hex,
    })
    
    recs_preview = _format_preview(recs_body_hex, preview_hex_chars)
    
    # Construir resultado
    result = {
        "recs_path": str(recs_path),
        "tesla_epoch": selected_epoch,
        "gst_sf": gst_sf,
        # GST seconds at which the satellite started transmitting this RECS
        # chunk's chips. Used downstream (SignalCorrelation) to align the
        # snapshot timeline with the reconstructed ECS replica.
        "gst_recs_start_s": gst_from_filename,
        "osnma_key_hex": selected_key,
        "aes_key_hex": aes_key_hex_str,
        "iv_hex": iv_hex,
        "P_vector": P_vector,
        "P_hex": P_hex,
        "P_dec": P_dec_string,
        "aes_key_dec": aes_key_dec_string,
        "iv_dec": iv_dec_string,
        "recs_hex_preview": recs_preview,
        "ecs_hex_preview": _format_preview(ecs_hex, preview_hex_chars),
        "ecs_polar_preview": _ecs_to_polar_samples(ecs_hex, preview_polar_samples),
        "full_ecs_path": str(output_path) if output_path else None,
        "metadata": metadata,
    }
    
    return _serialise_value(result)


# ---------------------------------------------------------------------------
# PIPELINE EXECUTION (CONTRACT 1.0)
# ---------------------------------------------------------------------------

def run_pipeline(
    config=None,
    inputs=None,
    globals=None,
    logger=print,
):
    """Schema-driven entry point.
    consumes: parsed_recs_files, recs_files, tesla_keys (svid_encrypted from globals)
    produces: ecs_hex, ecs_path, gst_recs_start_s
    """
    inputs = dict(inputs or {})
    g = dict(globals or {})
    svid_encrypted = (g.get("svid_encrypted") or "").strip()
    # Prefer the parsed unitary RECS list when available; fall back to whatever
    # is still under recs_files (e.g. when the parser is bypassed).
    recs_list = inputs.get("parsed_recs_files") or inputs.get("recs_files") or []
    raw = _run_pipeline_legacy(
        config_overrides=config,
        recs_enabled=bool(svid_encrypted),
        recs_files=list(recs_list),
        tesla_keys=inputs.get("tesla_keys"),
    )
    metadata = raw.get("metadata", {}) if isinstance(raw, dict) else {}
    ecs_hex = metadata.get("ecs_hex_full")
    ecs_path = raw.get("full_ecs_path") if isinstance(raw, dict) else None
    gst_recs_start_s = raw.get("gst_recs_start_s") if isinstance(raw, dict) else None
    return {
        "outputs": {
            "ecs_hex": ecs_hex,
            "ecs_path": ecs_path,
            "gst_recs_start_s": gst_recs_start_s,
        },
        "summary": {
            "skipped": raw.get("skipped") if isinstance(raw, dict) else False,
            "reason": raw.get("reason") if isinstance(raw, dict) else None,
            "target_epoch": raw.get("target_epoch") if isinstance(raw, dict) else None,
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


# ---------------------------------------------------------------------------
# EXPORTS
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "MODULE_FILE",
    "BASE_DIR",
    "CONFIG_JSON_DIR",
    "DEFAULT_JSON_DIR",
    
    # Configuration
    "load_schema",
    "load_defaults",
    "load_config",
    "save_config",
    
    # Pipeline
    "run_pipeline",
    
    # Utility functions (in case needed externally)
    "parseRecsFilename",
    "decryptRECSFromFile",
    "calculateIV",
    "find_key_at",
    "find_next_key",
    
    # Multi-PRN support
    "find_recs_for_prn",
    "decrypt_recs_for_prn",
]


# ---------------------------------------------------------------------------
# TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Module_RECSDecryption Test ===")
    print(f"Base dir: {BASE_DIR}")
    print(f"Config dir: {CONFIG_JSON_DIR}")
    print(f"Default dir: {DEFAULT_JSON_DIR}")
    
    try:
        schema = load_schema()
        print(f"\nSchema loaded: {schema.get('block_name', 'Unknown')}")
        print(f"Sections: {len(schema.get('sections', []))}")
        
        defaults = load_defaults()
        print(f"\nDefaults: {len(defaults)} parameters")
        for k, v in list(defaults.items())[:5]:
            print(f"  {k}: {v}")
        
        config = load_config()
        print(f"\nConfig loaded: {len(config)} parameters")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
