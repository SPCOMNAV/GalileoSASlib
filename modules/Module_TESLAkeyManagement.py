from __future__ import annotations

import csv
import time as _time
from io import StringIO
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    requests = None

from utils.utils import (
    load_json_file,
    save_json_file,
    console_log,
    BASE_DIR,
    SCHEMA_DIR,
    CONFIG_DIR,
    DATA_DIR,
    RESULTS_DIR,
    GST_WEEK_SECONDS,
)

################################################ CONSTANTS ################################################

# Input: original TESLA keys are in DATA_DIR
DEFAULT_MANUAL_XML = DATA_DIR / "OSNMA_TESLA_Keys" / "osnma_keys.xml"
SLOG_DATA_DIR = BASE_DIR / "data" / "SLOG"  # data/SLOG (not dataset-specific)
# Output: exported keys go to RESULTS_DIR
DEFAULT_OUTPUT_DIR = RESULTS_DIR / "OSNMA_TESLA_Keys"

# SAS Online Server Configuration
SAS_SERVER_BASE_URL = "https://217.111.132.9:50567"
SAS_SLOG_ENDPOINT = "/SLOG"

EPOCH_SHIFT = 30
HEX32_RE = re.compile(r"^[0-9A-Fa-f]{32}$")
# SLOG line pattern: matches lines with TESLA key data
# Example: TESLA,1380,432000,ABCD1234...  or similar formats
SLOG_TESLA_RE = re.compile(r"TESLA[,;\s]+(\d+)[,;\s]+(\d+)[,;\s]+([0-9A-Fa-f]{32})", re.IGNORECASE)


################################################# Schema / Config Loaders #################################################

def load_schema() -> Dict[str, Any]:
    """Load the block schema from JSON."""
    return load_json_file(SCHEMA_DIR / "TESLAkeyManagement_schema.json")


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
    stored = load_json_file(CONFIG_DIR / "TESLAkeyManagement.json")
    defaults.update(stored)
    return defaults


def save_config(config: Dict[str, Any]) -> None:
    """Persist config values."""
    save_json_file(CONFIG_DIR / "TESLAkeyManagement.json", config)


################################################# Helper functions #################################################

def _debug(msg: str) -> None:
    """Print debug message with block name prefix.
    
    Args:
        msg: Message to print.
    """
    console_log("TESLAkeyManagement", msg)


def _sorted_items(d: Dict[int, str]) -> List[Tuple[int, str]]:
    """Sort dictionary items by key (epoch).
    
    Args:
        d: Dictionary with integer keys (epochs) and string values (keys).
    
    Returns:
        List of tuples sorted by epoch in ascending order.
    """
    return sorted(d.items(), key=lambda x: x[0])


def filter_range(
    keys: Dict[int, str],
    after: Optional[int],
    before: Optional[int]
) -> Dict[int, str]:
    """Filter keys to those within a specified epoch range.
    
    Args:
        keys: Dictionary mapping epochs to TESLA keys.
        after: Minimum epoch (inclusive). If None, no lower bound.
        before: Maximum epoch (inclusive). If None, no upper bound.
    
    Returns:
        Filtered dictionary containing only keys within the range.
    """
    items = _sorted_items(keys)
    if after is not None:
        items = [it for it in items if it[0] >= after]
    if before is not None:
        items = [it for it in items if it[0] <= before]
    return dict(items)


def find_key_at(keys: Dict[int, str], t: int) -> Optional[Tuple[int, str]]:
    """Find the TESLA key with epoch closest to but not exceeding target time.
    
    Uses binary search for efficient lookup in sorted key list.
    
    Args:
        keys: Dictionary mapping epochs to TESLA keys.
        t: Target epoch (GST seconds) to search for.
    
    Returns:
        Tuple of (epoch, key) for the matching key, or None if no key found.
    """
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


############################################## # Export functions #################################################

def to_json(keys: Dict[int, str]) -> str:
    """Convert TESLA keys dictionary to JSON format string.
    
    Args:
        keys: Dictionary mapping epochs to TESLA keys.
    
    Returns:
        Formatted JSON string with array of {epoch, osnma_key} objects.
    """
    items = _sorted_items(keys)
    obj = [{"epoch": e, "osnma_key": k} for e, k in items]
    return json.dumps(obj, ensure_ascii=False, indent=2)


def to_csv(keys: Dict[int, str]) -> str:
    """Convert TESLA keys dictionary to CSV format string.
    
    Args:
        keys: Dictionary mapping epochs to TESLA keys.
    
    Returns:
        CSV string with header row 'epoch,osnma_key' followed by data rows.
    """
    items = _sorted_items(keys)
    lines = ["epoch,osnma_key"]
    for e, k in items:
        lines.append(f"{e},{k}")
    return "\n".join(lines)


##################################################### Parsing functions ####################################################

def parse_xml(xml_text: str) -> Dict[int, str]:
    """
    Parse OSNMA keys from XML text.
    Returns dict {epoch_GST_seconds: OSNMA_KEY(uppercase)}
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML: {e}") from e

    if root.tag != "OsnmaKeys":
        raise ValueError(f"Unexpected root tag: '{root.tag}' (expected 'OsnmaKeys')")

    out = {}
    for k in root.findall("key"):
        key_elem = k.find("osnma_key")
        epoch_elem = k.find("epoch")
        if key_elem is None or epoch_elem is None:
            raise ValueError("Incomplete <key> element: requires <osnma_key> and <epoch>")

        key_str = (key_elem.text or "").strip()
        epoch_str = (epoch_elem.text or "").strip()

        if not HEX32_RE.match(key_str):
            raise ValueError(f"Invalid OSNMA key (must be 32 hex chars): '{key_str}'")

        try:
            epoch_val = int(epoch_str)
        except ValueError as e:
            raise ValueError(f"epoch is not an integer: '{epoch_str}'") from e

        out[epoch_val] = key_str.upper()

    return out


def parse_csv(csv_text: str) -> Dict[int, str]:
    """Parse OSNMA keys from CSV text."""
    text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
    
    # Try comma delimiter first
    reader = csv.DictReader(StringIO(text))
    rows = list(reader) if reader.fieldnames else []

    # Try semicolon if comma didn't work
    if not rows or not reader.fieldnames or len(reader.fieldnames) == 1:
        reader = csv.DictReader(StringIO(text), delimiter=';')
        rows = list(reader) if reader.fieldnames else []

    if not rows:
        # Try simple CSV without headers
        simple = csv.reader(StringIO(text))
        out_simple = {}
        for r in simple:
            if not r or all(not c.strip() for c in r):
                continue
            if len(r) < 2:
                continue
            key_str = str(r[0]).strip()
            epoch_str = str(r[1]).strip()
            if not HEX32_RE.match(key_str):
                continue
            try:
                epoch_val = int(epoch_str)
            except ValueError:
                continue
            out_simple[epoch_val] = key_str.upper()
        if not out_simple:
            raise ValueError("CSV empty or no recognizable columns.")
        return out_simple

    # Map column names flexibly
    cols = [c.strip().lower() for c in (reader.fieldnames or [])]
    
    def _find(colnames: Sequence[str]) -> Optional[str]:
        for cand in colnames:
            if cand in cols:
                return reader.fieldnames[cols.index(cand)]
        return None

    key_col = _find(["teslakeyhex", "osnma_key", "tesla_key", "key"])
    epoch_col = _find(["teslakeygst", "epoch", "gst"])

    if not key_col or not epoch_col:
        raise ValueError(
            f"CSV without recognizable headers. Found: {reader.fieldnames}. "
            f"Need at least 'teslaKeyHex'/'osnma_key' and 'teslaKeyGst'/'epoch'/'gst'."
        )

    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key_str = str(row.get(key_col, "")).strip()
        epoch_str = str(row.get(epoch_col, "")).strip()
        if not key_str or not epoch_str:
            continue
        if not HEX32_RE.match(key_str):
            raise ValueError(f"Invalid OSNMA key (must be 32 hex chars): '{key_str}'")
        try:
            epoch_val = int(epoch_str)
        except ValueError as e:
            raise ValueError(f"epoch is not an integer: '{epoch_str}'") from e
        out[epoch_val] = key_str.upper()

    return out


def parse_osnmalib_json(json_text: str) -> Dict[int, str]:
    """
    Parse TESLA keys from OSNMAlib JSON format.
    
    The OSNMAlib ``/septentrio/subframe_json`` endpoint returns an array of
    subframes.  Each subframe contains:
    
    * ``verified_OSNMA_material.tesla_key`` – list of per-SV key entries, each
      with ``value`` (32-hex), ``GST`` ([WN, ToW]) and a ``verification`` flag.
    * ``OSNMA_material_received.<svid>.mack_data.tesla_key`` – the raw received
      key (same value for all SVs in one subframe epoch).
    
    We collect keys from **both** sections:
    
    1. Verified entries whose ``verification`` flag is ``True`` are preferred.
    2. All other entries (unverified or raw) are kept as fallback — the key
       value itself is authentic; the flag only indicates whether osnmalib has
       finished the chain-verification step at the moment the JSON was served.
    
    Returns dict ``{epoch_GST_seconds: OSNMA_KEY_hex_upper}``.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    
    if not isinstance(data, list):
        raise ValueError("OSNMAlib JSON should be an array of subframes")
    
    out: Dict[int, str] = {}        # verified keys  (high confidence)
    fallback: Dict[int, str] = {}   # unverified / raw keys
    
    for subframe in data:
        if not isinstance(subframe, dict):
            continue
        
        # ── 1. verified_OSNMA_material.tesla_key ────────────────────────
        verified = subframe.get("verified_OSNMA_material", {})
        tesla_keys = verified.get("tesla_key", [])
        
        for key_entry in tesla_keys:
            if not isinstance(key_entry, dict):
                continue
            
            value = key_entry.get("value", "")
            gst = key_entry.get("GST", [])
            
            if not value or not HEX32_RE.match(value):
                continue
            if len(gst) != 2:
                continue
            
            try:
                wn = int(gst[0])
                tow = int(gst[1])
                epoch_seconds = wn * GST_WEEK_SECONDS + tow
            except (TypeError, ValueError):
                continue
            
            if key_entry.get("verification", False):
                # Verified key – always accept (overwrites fallback)
                out[epoch_seconds] = value.upper()
            else:
                # Unverified but still valid value – store as fallback
                if epoch_seconds not in out:
                    fallback[epoch_seconds] = value.upper()
        
        # ── 2. OSNMA_material_received (raw received keys) ─────────────
        osnma_material = subframe.get("OSNMA_material_received", {})
        for svid, sat_data in osnma_material.items():
            if not isinstance(sat_data, dict):
                continue
            
            mack_data = sat_data.get("mack_data", {})
            tesla_key = mack_data.get("tesla_key")
            
            if tesla_key and HEX32_RE.match(tesla_key):
                metadata = subframe.get("metadata", {})
                gst = metadata.get("GST_subframe", [])
                
                if len(gst) == 2:
                    try:
                        wn = int(gst[0])
                        tow = int(gst[1])
                        epoch_seconds = wn * GST_WEEK_SECONDS + tow
                        if epoch_seconds not in out:
                            fallback[epoch_seconds] = tesla_key.upper()
                    except (TypeError, ValueError):
                        continue
    
    # Merge: verified keys take priority, fallback fills gaps
    merged = {**fallback, **out}
    return merged


def parse_slog(slog_text: str) -> Dict[int, str]:
    """
    Parse TESLA keys from SAS SLOG (Security LOG) format.
    
    SLOG files contain TESLA key entries in various formats. This parser
    handles multiple common formats:
    
    1. CSV-like: TESLA,WN,TOW,KEY_HEX
    2. Semicolon: TESLA;WN;TOW;KEY_HEX
    3. Space-separated: TESLA WN TOW KEY_HEX
    4. Key-value: WN=1380 TOW=432000 KEY=ABCD...
    
    The parser extracts WN (Week Number) and TOW (Time of Week) and converts
    to GST epoch seconds.
    
    Args:
        slog_text: Raw text content of SLOG file.
    
    Returns:
        Dict mapping epoch (GST seconds) to TESLA key (uppercase hex).
    """
    out = {}
    lines = slog_text.strip().split('\n')
    
    _debug(f"Parsing SLOG with {len(lines)} lines")
    
    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('//'):
            continue
        
        # Try pattern 1: TESLA,WN,TOW,KEY or TESLA;WN;TOW;KEY
        match = SLOG_TESLA_RE.search(line)
        if match:
            try:
                wn = int(match.group(1))
                tow = int(match.group(2))
                key_hex = match.group(3).upper()
                epoch_seconds = wn * GST_WEEK_SECONDS + tow
                out[epoch_seconds] = key_hex
                continue
            except ValueError:
                pass
        
        # Try pattern 2: parse as CSV/semicolon with flexible columns
        # Look for lines containing a 32-char hex string
        parts = re.split(r'[,;\s\t]+', line)
        key_hex = None
        wn = None
        tow = None
        
        for i, part in enumerate(parts):
            part = part.strip()
            
            # Check for 32-char hex (TESLA key)
            if HEX32_RE.match(part):
                key_hex = part.upper()
            
            # Check for WN/TOW patterns
            elif part.upper().startswith('WN='):
                try:
                    wn = int(part.split('=')[1])
                except (ValueError, IndexError):
                    pass
            elif part.upper().startswith('TOW='):
                try:
                    tow = int(part.split('=')[1])
                except (ValueError, IndexError):
                    pass
            elif part.upper().startswith('GST='):
                # GST might be in format WN.TOW or epoch seconds
                try:
                    gst_val = part.split('=')[1]
                    if '.' in gst_val:
                        wn, tow = gst_val.split('.')
                        wn, tow = int(wn), int(tow)
                    else:
                        epoch_seconds = int(gst_val)
                        if key_hex:
                            out[epoch_seconds] = key_hex
                            key_hex = None
                except (ValueError, IndexError):
                    pass
        
        # If we found a key and WN/TOW, add it
        if key_hex and wn is not None and tow is not None:
            epoch_seconds = wn * GST_WEEK_SECONDS + tow
            out[epoch_seconds] = key_hex
        
        # Try pattern 3: assume positional fields (skip first if label)
        # Common format: [label] WN TOW KEY [other fields]
        if not key_hex and len(parts) >= 3:
            # Try different starting positions
            for start in range(min(3, len(parts))):
                try:
                    if len(parts) > start + 2:
                        potential_wn = int(parts[start])
                        potential_tow = int(parts[start + 1])
                        potential_key = parts[start + 2]
                        
                        if HEX32_RE.match(potential_key):
                            # Sanity check: WN should be reasonable (1000-3000 for Galileo)
                            if 1000 <= potential_wn <= 3000 and 0 <= potential_tow < GST_WEEK_SECONDS:
                                epoch_seconds = potential_wn * GST_WEEK_SECONDS + potential_tow
                                out[epoch_seconds] = potential_key.upper()
                                break
                except (ValueError, IndexError):
                    pass
    
    _debug(f"Parsed {len(out)} TESLA keys from SLOG")
    return out


def download_slog_from_sas(
    provider: str = "GSCX",
    year: int = 25,
    day_of_year: int = 360,
    server_url: str = SAS_SERVER_BASE_URL,
    output_dir: Optional[Path] = None,
    timeout: int = 30,
    cert_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Download SLOG file from SAS online server.
    
    URL format: /SLOG/{PROVIDER}/{YEAR}/{FILENAME}
    Example: /SLOG/GSCX/25/GSCX01_25360.LOG
    
    Args:
        provider: Provider code (e.g., "GSCX")
        year: 2-digit year (e.g., 25 for 2025)
        day_of_year: Day of year (1-366)
        server_url: Base URL of SAS server
        output_dir: Directory to save file. Uses SLOG_DATA_DIR if None.
        timeout: Request timeout in seconds
        cert_path: Path to server certificate for SSL verification
    
    Returns:
        Dict with success status, file path, and parsed keys.
    """
    if requests is None:
        return {
            "success": False,
            "error": "requests library not available",
            "keys": {},
        }
    
    out_dir = output_dir or SLOG_DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Build filename: GSCX01_25360.LOG
    filename = f"{provider}01_{year:02d}{day_of_year:03d}.LOG"
    url = f"{server_url.rstrip('/')}{SAS_SLOG_ENDPOINT}/{provider}/{year:02d}/{filename}"
    output_path = out_dir / filename
    
    verify = False  # Default: skip verification for self-signed cert
    if cert_path and cert_path.exists():
        verify = str(cert_path)
    
    try:
        _debug(f"Downloading SLOG from {url}")
        response = requests.get(url, timeout=timeout, verify=verify)
        response.raise_for_status()
        
        # Save file
        output_path.write_bytes(response.content)
        _debug(f"SLOG saved to: {output_path}")
        
        # Parse TESLA keys
        slog_text = response.content.decode('utf-8', errors='replace')
        keys = parse_slog(slog_text)
        
        return {
            "success": True,
            "file_path": str(output_path),
            "keys": keys,
            "key_count": len(keys),
            "url": url,
        }
        
    except Exception as e:
        _debug(f"SLOG download failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "keys": {},
            "url": url,
        }


def fetch_keys_from_sas_slog(
    provider: str = "GSCX",
    year: int = 25,
    day_of_year: int = 360,
    server_url: str = SAS_SERVER_BASE_URL,
    timeout: int = 30,
    cert_path: Optional[Path] = None,
) -> Dict[int, str]:
    """
    Fetch TESLA keys by downloading SLOG from SAS server.
    
    This is a convenience function that downloads the SLOG and returns
    just the parsed keys (for compatibility with other fetch functions).
    
    Args:
        provider: Provider code (e.g., "GSCX")
        year: 2-digit year (e.g., 25 for 2025)
        day_of_year: Day of year (1-366)
        server_url: Base URL of SAS server
        timeout: Request timeout in seconds
        cert_path: Path to server certificate
    
    Returns:
        Dict mapping epoch (GST seconds) to TESLA key.
    
    Raises:
        RuntimeError: If download or parsing fails.
    """
    result = download_slog_from_sas(
        provider=provider,
        year=year,
        day_of_year=day_of_year,
        server_url=server_url,
        timeout=timeout,
        cert_path=cert_path,
    )
    
    if not result["success"]:
        raise RuntimeError(f"SLOG download failed: {result.get('error', 'Unknown error')}")
    
    return result["keys"]


################################################# Fetching functions #################################################

def fetch_keys_from_url(url: str, timeout: float = 10.0) -> Dict[int, str]:
    """Fetch and parse keys from URL (XML, CSV, or OSNMAlib JSON)."""
    if requests is None:
        raise RuntimeError("requests library not available for URL fetch")
    
    _debug(f"Fetching keys from: {url}")
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    
    content = response.text
    
    # Try OSNMAlib JSON format first (for osnmalib.eu endpoints)
    if "osnmalib.eu" in url or "subframe_json" in url:
        try:
            keys = parse_osnmalib_json(content)
            if keys:
                _debug(f"Parsed {len(keys)} keys from OSNMAlib JSON")
                return keys
        except ValueError as e:
            _debug(f"OSNMAlib JSON parse failed: {e}")
    
    # Try XML
    try:
        return parse_xml(content)
    except ValueError:
        pass
    
    # Try CSV
    try:
        return parse_csv(content)
    except ValueError:
        pass
    
    # Try generic JSON (OSNMAlib format)
    try:
        return parse_osnmalib_json(content)
    except ValueError:
        pass
    
    raise ValueError("Could not parse response as XML, CSV, or OSNMAlib JSON")


def fetch_keys_multi(
    urls: List[str],
    duration_s: float = 300.0,
    interval_s: float = 30.0,
    timeout: float = 10.0,
) -> Dict[int, str]:
    """Poll multiple OSNMA endpoints over *duration_s* seconds to accumulate
    as many TESLA keys as possible.

    Every *interval_s* seconds each URL is fetched and new keys are merged.
    A new subframe appears every ~30 s, so polling for 5 min should yield
    ~20–22 unique keys (2 sources × ~12 keys each, with overlap).

    Args:
        urls: List of OSNMA JSON endpoints to poll.
        duration_s: Total polling time in seconds (0 = single fetch).
        interval_s: Seconds between consecutive poll rounds.
        timeout: HTTP request timeout per URL.

    Returns:
        Dict mapping epoch (GST seconds) → TESLA key hex (upper).
    """
    if requests is None:
        raise RuntimeError("requests library not available for URL fetch")

    accumulated: Dict[int, str] = {}
    t_start = _time.time()
    round_num = 0

    while True:
        round_num += 1
        before = len(accumulated)
        for url in urls:
            try:
                _debug(f"  [poll #{round_num}] Fetching {url} ...")
                keys = fetch_keys_from_url(url, timeout=timeout)
                accumulated.update(keys)
                _debug(f"  [poll #{round_num}] Got {len(keys)} keys from {url} "
                       f"(total unique: {len(accumulated)})")
            except Exception as exc:
                _debug(f"  [poll #{round_num}] Error from {url}: {exc}")
        new_keys = len(accumulated) - before
        _debug(f"  [poll #{round_num}] +{new_keys} new keys this round "
               f"(total: {len(accumulated)})")

        elapsed = _time.time() - t_start
        if elapsed >= duration_s:
            break
        remaining = duration_s - elapsed
        sleep_s = min(interval_s, remaining)
        if sleep_s > 0:
            _debug(f"  Sleeping {sleep_s:.0f}s ({remaining:.0f}s remaining) ...")
            _time.sleep(sleep_s)

    _debug(f"Multi-fetch done: {len(accumulated)} unique keys after "
           f"{round_num} round(s) in {_time.time()-t_start:.1f}s")
    return accumulated


def parse_simple_json(json_text: str) -> Dict[int, str]:
    """Parse the simple ``[{"epoch": int, "osnma_key": "<hex>"}, ...]`` format
    written by ``to_json`` (the inverse of that exporter)."""
    data = json.loads(json_text)
    if not isinstance(data, list):
        raise ValueError("Simple TESLA JSON must be a list of {epoch, osnma_key} objects")
    out: Dict[int, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        epoch = entry.get("epoch") or entry.get("gst") or entry.get("teslaKeyGst")
        key = entry.get("osnma_key") or entry.get("teslaKeyHex") or entry.get("tesla_key") or entry.get("key")
        if epoch is None or key is None:
            continue
        out[int(epoch)] = str(key).upper()
    if not out:
        raise ValueError("Simple TESLA JSON contained no usable {epoch, osnma_key} entries")
    return out


def fetch_keys_from_file(path: Path) -> Dict[int, str]:
    """Load and parse keys from local file."""
    if not path.exists():
        raise FileNotFoundError(f"Key file not found: {path}")

    _debug(f"Loading keys from: {path}")
    content = path.read_text(encoding="utf-8")

    # Determine format from extension or content
    suffix = path.suffix.lower()

    if suffix == ".xml":
        return parse_xml(content)
    elif suffix == ".csv":
        return parse_csv(content)
    elif suffix == ".json":
        try:
            return parse_simple_json(content)
        except ValueError:
            return parse_osnmalib_json(content)
    else:
        # Try in order: XML, CSV, simple JSON, OSNMAlib JSON
        for parser in (parse_xml, parse_csv, parse_simple_json, parse_osnmalib_json):
            try:
                return parser(content)
            except ValueError:
                continue
        raise ValueError(f"Could not parse {path.name} as XML/CSV/JSON")


################################################# Pipeline execution ##############################################################

def _run_pipeline_legacy(
    config: Optional[Mapping[str, Any]] = None,
    *,
    manual_mode: Optional[bool] = None,
    manual_keys_path: Optional[str] = None,
    sas_online_mode: Optional[bool] = None,
    auth_periodicity_sec: Optional[float] = None,
    auth_duration_sec: Optional[float] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Run the TESLA key management pipeline.
    
    Reads its own JSON config. Overrides can be provided via config parameter
    or keyword arguments (from SASpipeline).
    
    Modes (in priority order):
    1. sas_online_mode=True: Download from SAS server SLOG
    2. manual_mode=True: Use local file
    3. manual_mode=False: Download from OSNMA URL
    
    Args:
        config: Optional configuration mapping to override JSON config.
        manual_mode: If True, use local file; if False, download from URL.
                    Derived from ConfigInit.tesla_file_enabled by SASpipeline.
        manual_keys_path: Path to local TESLA keys file.
                         Derived from ConfigInit.tesla_file_path by SASpipeline.
        sas_online_mode: If True, download from SAS server SLOG.
        auth_periodicity_sec: Authentication periodicity from ConfigInit (seconds).
                             Used to compute optimal multi-fetch polling duration.
        auth_duration_sec: Authentication duration from ConfigInit (seconds).
        **kwargs: Additional keyword arguments (ignored for forward compatibility).
    
    Returns:
        Dictionary with keys and metadata
    """
    # Load config from own JSON, then apply overrides
    base_config = load_config()
    if config:
        base_config.update(config)
    data = base_config
    
    # Priority: explicit kwargs > config dict > defaults
    # manual_mode from kwargs (SASpipeline) takes precedence
    if manual_mode is not None:
        data["manual_mode"] = manual_mode
    if manual_keys_path is not None:
        data["manual_keys_path"] = manual_keys_path
    if sas_online_mode is not None:
        data["sas_online_enabled"] = sas_online_mode
    
    # Extract configuration values
    _manual_mode = bool(data.get("manual_mode", True))
    _sas_online_enabled = bool(data.get("sas_online_enabled", False))
    _manual_keys_path = data.get("manual_keys_path", "data/datasetION/OSNMA_TESLA_Keys/osnma_keys.xml")
    _default_osnma_url = "https://osnmalib.eu/septentrio/subframe_json"
    osnma_url = data.get("osnma_url", "") or _default_osnma_url  # fallback if empty string
    
    # SAS Online configuration
    sas_server_url = data.get("sas_server_url", SAS_SERVER_BASE_URL)
    sas_provider = data.get("sas_provider", "GSCX")
    sas_year = int(data.get("sas_year", 25))
    sas_day = int(data.get("sas_day_of_year", 360))
    sas_timeout = int(data.get("sas_timeout", 30))
    
    filter_enabled = bool(data.get("filter_enabled", False))
    epoch_after = data.get("epoch_after")
    epoch_before = data.get("epoch_before")
    export_format = str(data.get("export_format", "json")).lower()
    output_directory = data.get("output_directory", "")
    
    # Resolve output directory - use execution-specific directory if available
    if output_directory:
        output_dir = Path(output_directory)
        if not output_dir.is_absolute():
            output_dir = (BASE_DIR / output_dir).resolve()
    else:
        # Use execution-specific results directory if available
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        if ctx.get_execution_id():
            output_dir = ctx.get_execution_results_dir("TESLA_keys")
        else:
            output_dir = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Fetch keys based on mode
    keys = {}
    source_desc = ""
    slog_result = None
    
    try:
        if _sas_online_enabled:
            # Download from SAS server SLOG
            _debug(f"SAS Online mode: downloading SLOG from {sas_server_url}")
            slog_result = download_slog_from_sas(
                provider=sas_provider,
                year=sas_year,
                day_of_year=sas_day,
                server_url=sas_server_url,
                timeout=sas_timeout,
            )
            if slog_result["success"]:
                keys = slog_result["keys"]
                source_desc = f"SAS SLOG: {slog_result.get('url', sas_server_url)}"
                _debug(f"SAS Online mode: downloaded {len(keys)} keys from SLOG")
            else:
                raise RuntimeError(slog_result.get("error", "SLOG download failed"))
        
        elif _manual_mode:
            # Use local file
            file_path = Path(_manual_keys_path)
            if not file_path.is_absolute():
                file_path = (BASE_DIR / file_path).resolve()
            
            # Check if it's a SLOG file
            if file_path.suffix.upper() == '.LOG':
                slog_text = file_path.read_text(encoding='utf-8', errors='replace')
                keys = parse_slog(slog_text)
                source_desc = f"Local SLOG: {file_path}"
            else:
                keys = fetch_keys_from_file(file_path)
                source_desc = f"Local: {file_path}"
            _debug(f"Manual mode: loaded {len(keys)} keys from {file_path}")
        else:
            # ── Multi-fetch mode: poll several URLs over time ──────────
            _multi_fetch = bool(data.get("multi_fetch_enabled", False))
            _fetch_duration = float(data.get("multi_fetch_duration_s", 300))
            _fetch_interval = float(data.get("multi_fetch_interval_s", 28))
            _extra_urls_raw = data.get("extra_osnma_urls", [])
            if isinstance(_extra_urls_raw, str):
                _extra_urls_raw = [u.strip() for u in _extra_urls_raw.split(",") if u.strip()]

            all_urls = [osnma_url] + [u for u in _extra_urls_raw if u != osnma_url]

            # Auto-compute duration from auth_periodicity if not explicitly set
            # OSNMA disclosure delay ≈ 10 subframes ≈ 300s.
            # We need keys covering: snapshot_time - disclosure_delay → snapshot_time + auth_duration.
            # The first fetch already gives ~12 keys (~6 min). Extra polling
            # accumulates ~1 new key per 30s.  A good heuristic:
            #   fetch_duration = max(config_value, auth_duration + auth_periodicity * 2)
            # so we always wait long enough for the OSNMA pipeline to push
            # the keys that cover our snapshot window.
            _auth_period = auth_periodicity_sec if auth_periodicity_sec is not None else 30.0
            _auth_dur = auth_duration_sec if auth_duration_sec is not None else 30.0
            _auto_duration = max(_auth_dur + _auth_period * 4, 90.0)
            if _fetch_duration <= 0:
                _fetch_duration = _auto_duration
                _debug(f"Auto-computed multi-fetch duration from auth_periodicity="
                       f"{_auth_period}s, auth_duration={_auth_dur}s → {_fetch_duration:.0f}s")

            if _multi_fetch and len(all_urls) > 0:
                _debug(f"Multi-fetch mode: {len(all_urls)} URLs, "
                       f"duration={_fetch_duration:.0f}s, interval={_fetch_interval}s")
                keys = fetch_keys_multi(
                    urls=all_urls,
                    duration_s=_fetch_duration,
                    interval_s=_fetch_interval,
                )
                source_desc = f"Multi-fetch ({len(all_urls)} URLs, {_fetch_duration}s): {', '.join(all_urls)}"
                _debug(f"Multi-fetch: accumulated {len(keys)} unique keys")
            else:
                # Single fetch (original behaviour)
                keys = fetch_keys_from_url(osnma_url)
                source_desc = f"URL: {osnma_url}"
                _debug(f"Online mode: downloaded {len(keys)} keys from {osnma_url}")
    except Exception as e:
        _debug(f"Error fetching keys: {e}")
        return {
            "success": False,
            "error": str(e),
            "keys": {},
            "key_count": 0,
        }
    
    # Apply filter
    if filter_enabled:
        after_val = int(epoch_after) if epoch_after is not None else None
        before_val = int(epoch_before) if epoch_before is not None else None
        keys = filter_range(keys, after_val, before_val)
    
    # Export
    output_files = {}
    if keys and export_format:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if export_format == "json":
            json_path = output_dir / f"tesla_keys_{timestamp}.json"
            json_path.write_text(to_json(keys), encoding="utf-8")
            output_files["json"] = str(json_path)
            
        elif export_format == "csv":
            csv_path = output_dir / f"tesla_keys_{timestamp}.csv"
            csv_path.write_text(to_csv(keys), encoding="utf-8")
            output_files["csv"] = str(csv_path)
    
    return {
        "success": True,
        "source": source_desc,
        "keys": {int(e): k for e, k in _sorted_items(keys)},
        "key_count": len(keys),
        "filter_applied": filter_enabled,
        "output_files": output_files,
        "executed_at": datetime.now(timezone.utc).isoformat() + "Z",
    }


def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: (none — all upstream values come from globals)
    produces: tesla_keys
    """
    g = dict(globals or {})
    raw = _run_pipeline_legacy(
        config=config,
        manual_mode=bool(g.get("tesla_file_enabled", True)),
        manual_keys_path=g.get("tesla_file_path") or None,
        auth_periodicity_sec=g.get("auth_periodicity_sec"),
        auth_duration_sec=g.get("auth_duration_sec"),
    )
    return {
        "outputs": {
            "tesla_keys": raw.get("keys", {}),
        },
        "summary": {
            "success": raw.get("success"),
            "key_count": raw.get("key_count"),
            "source": raw.get("source"),
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


def run_pipeline_from_json(payload: Union[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Execute the TESLA pipeline from a JSON-compatible payload."""
    if isinstance(payload, str):
        try:
            config_map = json.loads(payload.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload for TESLA pipeline") from exc
    elif isinstance(payload, Mapping):
        config_map = payload
    else:
        raise TypeError("TESLA payload must be a mapping or JSON string")

    return run_pipeline(config_map)


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "load_schema",
    "load_config",
    "save_config",
    "run_pipeline",
    "run_pipeline_from_json",
    "fetch_keys_from_url",
    "fetch_keys_from_file",
    "fetch_keys_from_sas_slog",
    "download_slog_from_sas",
    "parse_xml",
    "parse_csv",
    "parse_osnmalib_json",
    "parse_slog",
    "to_json",
    "to_csv",
    "filter_range",
    "find_key_at",
    "SAS_SERVER_BASE_URL",
    "SLOG_DATA_DIR",
]
