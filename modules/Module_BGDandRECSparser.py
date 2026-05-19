from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple, Union

from utils.utils import (
    load_json_file,
    save_json_file,
    console_log,
    serialise_value,
    BASE_DIR,
    SCHEMA_DIR,
    CONFIG_DIR,
    DATA_DIR,
    RESULTS_DIR,
    GST_EPOCH,
    GST_WEEK_SECONDS,
    gst_to_recs_timestamp,
    generate_recs_filename,
    week_tow_to_gst_seconds,
)

# Import online RECS header parsing
from modules.Module_BGDandRECSdownloader import (
    parse_recs_header,
    RECSHeader,
    RECS_ONLINE_DIR,
)

################################################ CONSTANTS ################################################

RECS_DATA_DIR = DATA_DIR / "RECS_Unitary"

DEFAULT_OUTPUT_DIR = RESULTS_DIR / "RECS_parsed"

# Header sizes for different RECS formats
RECS_HEADER_SIZE_OLD = 16   # Old .RCS format
RECS_HEADER_SIZE_BIN = 25   # New online .bin format

# Full unitary RECS body size: 81920 chips = 10240 bytes
FULL_UNITARY_CHIPS = 81920
FULL_UNITARY_BYTES = FULL_UNITARY_CHIPS // 8  # 10240

# Deciseconds in a GST week (604800 seconds × 10)
WEEK_DECISECONDS = GST_WEEK_SECONDS * 10  # 6_048_000

# ── NChip lookup table (Galileo SAS Spec Table 9) ──────────────────────────────
# Maps the NChip code in the aggregated header to the chip count.
NCHIP_TABLE: Dict[int, int] = {
    0: 5_120,    # 1.001 ms
    1: 10_240,   # 2.002 ms
    2: 20_480,   # 4.004 ms
    3: 40_960,   # 8.008 ms
    4: 81_920,   # 16.006 ms (full unitary RECS)
}

################################################ ERRORS ################################################

class ParseError(RuntimeError):
    """Error during parsing operations."""
    pass

################################################# Schema / Config loaders #################################################

def load_schema() -> Dict[str, Any]:
    """Load the block schema from JSON."""
    return load_json_file(SCHEMA_DIR / "BGDandRECSparser_schema.json")


def load_config() -> Dict[str, Any]:
    """Load current config values."""
    return load_json_file(CONFIG_DIR / "BGDandRECSparser.json")


def save_config(config: Dict[str, Any]) -> None:
    """Persist config values."""
    save_json_file(CONFIG_DIR / "BGDandRECSparser.json", config)


################################################# Helper functions #################################################

def _debug(msg: str) -> None:
    """Print debug message with block name prefix.
    
    Args:
        msg: Debug message to print.
    """
    console_log("BGDandRECSParser", msg)


def _iter_recs_files(candidates: Optional[object]) -> List[Path]:
    """Resolve RECS file paths from various input formats.
    
    Only processes explicitly provided files - no automatic directory scanning.
    This ensures only RECS files from ExecutionContext (downloader) are processed.
    
    Args:
        candidates: Input specification (path string, Path object, or list of paths).
                   None or empty list returns empty list (no files to process).
    
    Returns:
        List of resolved absolute Path objects.
    
    Raises:
        TypeError: If candidates type is not supported.
        ParseError: If any specified file is missing.
    """
    # No automatic scanning - only process explicitly provided files
    if candidates is None:
        return []
    
    resolved = []
    if isinstance(candidates, (str, Path)):
        resolved = [Path(candidates)]
    elif isinstance(candidates, Sequence) and not isinstance(candidates, str):
        for item in candidates:
            resolved.append(Path(item))
    else:
        raise TypeError("Unsupported RECS file specification")

    if not resolved:
        return []

    unique_paths = []
    seen = set()
    for path in resolved:
        if path.is_absolute():
            absolute = path.resolve()
        else:
            candidate = (RECS_DATA_DIR / path).resolve()
            if not candidate.exists():
                candidate = (BASE_DIR / path).resolve()
            absolute = candidate
        
        if absolute not in seen:
            if not absolute.exists():
                raise ParseError(f"Missing RECS file: {absolute}")
            seen.add(absolute)
            unique_paths.append(absolute)

    return unique_paths


############################################## NChip HELPERS ##############################################

def nchip_to_chips(nchip_code: int) -> int:
    """Look up the chip count for a given NChip code (Galileo SAS Spec Table 9).

    Args:
        nchip_code: NChip value from the aggregated RECS header (0–4).

    Returns:
        Number of chips per unitary RECS.

    Raises:
        ParseError: If the code is reserved / unknown.
    """
    if nchip_code not in NCHIP_TABLE:
        raise ParseError(
            f"Unknown NChip code {nchip_code}. "
            f"Valid codes: {sorted(NCHIP_TABLE.keys())}"
        )
    return NCHIP_TABLE[nchip_code]


def nchip_to_bytes(nchip_code: int) -> int:
    """Return the byte length of one unitary RECS body for *nchip_code*."""
    return nchip_to_chips(nchip_code) // 8


############################################## HEADER BUILDERS ##############################################

def build_unitary_header(
    provider: str,
    intv: int,
    wn: int,
    atow_decisec: int,
    svid: int,
    kdi: int,
    rand_flag: int,
    file_version: int,
) -> bytes:
    """Build a 16-byte unitary RECS header (.RCS format).

    Bit layout (128 bits = 16 bytes, big-endian):
        Provider    32 bits  (4 ASCII chars)
        IntV         8 bits
        Reserved     4 bits  (set to 0)
        WN          12 bits  (mod 4096)
        ATOW        24 bits  (deciseconds within the week)
        SVID         8 bits
        KDI          8 bits
        RAND         8 bits
        FileVersion  8 bits
        HeaderLength 16 bits (0x0010 = 16)

    Args:
        provider: 4-char ASCII provider string.
        intv: Interface version.
        wn: GST week number (will be taken mod 4096 for 12-bit field).
        atow_decisec: Adapted Time-of-Week in deciseconds.
        svid: Satellite vehicle ID.
        kdi: Key Delay Indicator.
        rand_flag: Randomisation flag.
        file_version: File format version.

    Returns:
        16-byte header as *bytes*.
    """
    # Provider — exactly 4 ASCII bytes
    prov_bytes = provider[:4].ljust(4).encode("ascii")

    # WN fits in 12 bits → mod 4096
    wn12 = wn % 4096

    # Reserved (4 bits) + WN high nibble
    reserved = 0
    byte5 = ((reserved & 0x0F) << 4) | ((wn12 >> 8) & 0x0F)
    byte6 = wn12 & 0xFF

    # ATOW: 24-bit big-endian
    atow_bytes = atow_decisec.to_bytes(3, "big")

    # HeaderLength = 16 (0x0010)
    header_len = struct.pack(">H", 0x0010)

    hdr = bytearray(16)
    hdr[0:4] = prov_bytes
    hdr[4] = intv & 0xFF
    hdr[5] = byte5
    hdr[6] = byte6
    hdr[7:10] = atow_bytes
    hdr[10] = svid & 0xFF
    hdr[11] = kdi & 0xFF
    hdr[12] = rand_flag & 0xFF
    hdr[13] = file_version & 0xFF
    hdr[14:16] = header_len

    return bytes(hdr)


############################################## BGD FILE PARSING ##############################################

# ── BGD file format (Galileo SAS specification) ─────────────────────────────────
#
# The .BGD file from gsc-europa.eu contains per-satellite BGD(E1,E6) values.
# This is the Broadcast Group Delay between E1 and E6 signal paths, which is
# a satellite-specific hardware bias that must be corrected BEFORE the Galileo SAS
# authentication test (Eq. 14 of the TAES 2023 paper).
#
# File structure (191 bytes typical):
#   Bytes  0- 3: Provider        (4 ASCII chars, e.g. "GSCX")
#   Byte      4: IntV            (Interface Version, e.g. 1)
#   Bytes  5-15: Header fields   (WN, TOW, validity, etc.)
#   Bytes 16-35: Satellite mask  (160 bits, MSB-first — 1 = SVID has valid BGD)
#   Bytes 36-71: Reserved/filler (36 bytes of 0x12)
#   Bytes 72+  : BGD data        (LE int16 per SVID, scale = 2^-32 seconds)
#
# The BGD data block contains 30 values indexed directly by SVID (1-30).
# SVID N is at position (N-1) in the data array.
# The satellite mask indicates which SVIDs have VALID data; non-masked
# SVIDs may contain stale values and should be treated as unavailable.
#
# Conversion:  BGD_meters = raw_value × 2^-32 × c
#   where c = 299792458 m/s (speed of light)
#
# Example: raw=192 → 192 × 2^-32 × c = 13.40 m
# ─────────────────────────────────────────────────────────────────────────

# BGD file constants
BGD_HEADER_SIZE = 72            # Header + mask + filler
BGD_SCALE_FACTOR = 2 ** -32     # Seconds per LSB (Galileo ICD convention)
BGD_MAX_SVIDS = 30              # Maximum SVIDs in data block
BGD_SPEED_OF_LIGHT = 299792458.0  # m/s

BGDValue = NamedTuple("BGDValue", [
    ("svid", int),
    ("raw", int),
    ("seconds", float),
    ("meters", float),
    ("valid", bool),
])


def parse_bgd_file(source: Path) -> Dict[str, Any]:
    """Parse a Galileo SAS .BGD file to extract per-satellite BGD(E1,E6) values.

    The BGD(E1,E6) is the satellite hardware group delay between the E1 and E6
    signal paths. It must be applied as a deterministic correction (Eq. 14)
    before the Galileo SAS authentication test, NOT just modeled as uncertainty.

    Args:
        source: Path to the .BGD file.

    Returns:
        Dict with keys:
            - ``source_path``: Original file path.
            - ``provider``: 4-char provider string (e.g. "GSCX").
            - ``interface_version``: IntV byte.
            - ``satellite_mask``: List of SVIDs with valid BGD data.
            - ``bgd_values``: Dict mapping SVID (int) → BGDValue namedtuple.
            - ``bgd_meters``: Dict mapping SVID (int) → BGD in meters (float).
                              Only includes SVIDs present in the satellite mask.

    Raises:
        ParseError: If the file is too small or has an invalid format.
    """
    _debug(f"Parsing BGD file: {source.name}")

    raw = source.read_bytes()
    if len(raw) < BGD_HEADER_SIZE + 2:
        raise ParseError(
            f"BGD file too small: {len(raw)} bytes (need ≥{BGD_HEADER_SIZE + 2})"
        )

    # ── Parse header ────────────────────────────────────────────────────
    provider = raw[0:4].decode("ascii", errors="replace")
    intv = raw[4]

    # ── Parse satellite mask (bytes 16-35, 160 bits, MSB-first) ─────────
    mask_bytes = raw[16:36]
    svids_in_mask: List[int] = []
    for byte_idx, byte_val in enumerate(mask_bytes):
        for bit_idx in range(8):
            if byte_val & (0x80 >> bit_idx):
                svid = byte_idx * 8 + bit_idx + 1
                if svid <= BGD_MAX_SVIDS:
                    svids_in_mask.append(svid)

    _debug(f"  Provider={provider}, IntV={intv}, "
           f"SVIDs in mask: {svids_in_mask} ({len(svids_in_mask)} total)")

    # ── Parse BGD data (LE int16 per SVID, indexed directly 1-30) ───────
    data_start = BGD_HEADER_SIZE
    n_values = min(BGD_MAX_SVIDS, (len(raw) - data_start) // 2)

    bgd_values: Dict[int, BGDValue] = {}
    bgd_meters: Dict[int, float] = {}

    for idx in range(n_values):
        offset = data_start + idx * 2
        raw_val = struct.unpack_from("<h", raw, offset)[0]
        svid = idx + 1
        bgd_sec = raw_val * BGD_SCALE_FACTOR
        bgd_m = bgd_sec * BGD_SPEED_OF_LIGHT
        is_valid = svid in svids_in_mask

        val = BGDValue(
            svid=svid,
            raw=raw_val,
            seconds=bgd_sec,
            meters=bgd_m,
            valid=is_valid,
        )
        bgd_values[svid] = val

        if is_valid:
            bgd_meters[svid] = bgd_m
            _debug(f"  SVID {svid:2d}: raw={raw_val:+6d}, "
                   f"BGD(E1,E6)={bgd_m:+8.2f} m  ({bgd_sec*1e9:+.2f} ns)")

    return {
        "source_path": str(source),
        "provider": provider,
        "interface_version": intv,
        "satellite_mask": svids_in_mask,
        "bgd_values": bgd_values,
        "bgd_meters": bgd_meters,
    }


############################################## CORE PARSING ##############################################

def detect_recs_format(source: Path) -> Tuple[str, int]:
    """Detect RECS file format based on extension and content.

    Args:
        source: Path to the RECS file.

    Returns:
        Tuple of (format_name, header_size) where format_name is 'old' or 'bin'.
    """
    ext = source.suffix.lower()
    if ext == ".bin":
        return ("bin", RECS_HEADER_SIZE_BIN)
    elif ext in (".rcs", ".recs"):
        return ("old", RECS_HEADER_SIZE_OLD)
    else:
        return ("old", RECS_HEADER_SIZE_OLD)


def parse_aggregated_recs(
    source: Path,
    *,
    output_directory: Optional[Path] = None,
    zero_pad_to_full: bool = True,
) -> Dict[str, Any]:
    """Parse an aggregated RECS *.bin* file into individual unitary *.RCS* files.

    Processing steps:
        1. Read and parse the 25-byte aggregated header.
        2. Look up the per-RECS chip count via NChip (Table 9).
        3. Split the concatenated body into individual RECS records.
        4. For each record, compute the GST timestamp (ATOW + i × RTBA).
        5. Build a 16-byte unitary header and save as ``XXXXII_YYDDDHHMMSSS_ss_K_R_VV.RCS``.

    If *zero_pad_to_full* is True and NChip < 81920, each unitary body
    is right-padded with zero bytes to the full 10 240 bytes (81 920 chips)
    so that downstream blocks always receive fixed-size RECS bodies.

    Args:
        source: Path to the aggregated ``.bin`` file.
        output_directory: Where to write the unitary files.
                          Defaults to ``source.parent``.
        zero_pad_to_full: Pad each unitary body to 10 240 bytes.

    Returns:
        Dict with keys:
            - ``source_path``: Original aggregated file path.
            - ``header``: Parsed aggregated header (dict).
            - ``n_recs``: Number of unitary RECS extracted.
            - ``nchip_code``: NChip index from header.
            - ``chips_per_recs``: Chip count per RECS.
            - ``bytes_per_recs_raw``: Raw byte count per RECS (before padding).
            - ``unitary_files``: List of dicts, each with ``path``, ``gst_seconds``,
              ``atow_decisec``, ``wn``, ``svid``, ``index``.

    Raises:
        ParseError: On malformed data or remaining bytes after splitting.
    """
    _debug(f"Parsing aggregated RECS file: {source.name}")

    raw = source.read_bytes()
    if len(raw) < RECS_HEADER_SIZE_BIN:
        raise ParseError(
            f"File too small for aggregated header: {len(raw)} bytes "
            f"(need {RECS_HEADER_SIZE_BIN})"
        )

    # ── 1. Parse header ─────────────────────────────────────────────────
    header = parse_recs_header(raw)
    if header is None:
        raise ParseError(f"Failed to parse header of {source.name}")

    _debug(
        f"  Header: Provider={header.provider}, SVID={header.svid}, "
        f"WN={header.wn}, ATOW={header.atow} dsec, "
        f"Duration={header.duration} dsec, NChip={header.nchip}, "
        f"RTBA={header.rtba} dsec, KDI={header.kdi}, RAND={header.rand}"
    )

    # ── 2. Determine per-RECS size ──────────────────────────────────────
    chips = nchip_to_chips(header.nchip)
    bytes_per_recs = chips // 8

    payload = raw[RECS_HEADER_SIZE_BIN:]
    if len(payload) == 0:
        raise ParseError("Aggregated RECS file has no payload data")

    if len(payload) % bytes_per_recs != 0:
        # Fallback: try with full 81920 chips (server may ignore nchip)
        if len(payload) % FULL_UNITARY_BYTES == 0:
            _debug(
                f"  Payload not divisible by {bytes_per_recs} bytes "
                f"(nchip={header.nchip}). Using full {FULL_UNITARY_BYTES} bytes."
            )
            chips = FULL_UNITARY_CHIPS
            bytes_per_recs = FULL_UNITARY_BYTES
        else:
            raise ParseError(
                f"Payload {len(payload)} bytes is not a multiple of "
                f"per-RECS size {bytes_per_recs} bytes (nchip={header.nchip})"
            )

    n_recs = len(payload) // bytes_per_recs
    _debug(
        f"  Payload: {len(payload)} bytes, {chips} chips/RECS, "
        f"{bytes_per_recs} bytes/RECS → {n_recs} unitary RECS"
    )

    # ── 3. Resolve output directory ─────────────────────────────────────
    out_dir = output_directory or source.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 4. Split & save individual RECS ─────────────────────────────────
    # Provider for filenames: first 4 chars of aggregated provider
    provider4 = header.provider[:4].ljust(4)
    intv_str = f"{header.interface_version:02d}"

    unitary_files: List[Dict[str, Any]] = []

    for idx in range(n_recs):
        # Extract the raw body for this RECS
        start = idx * bytes_per_recs
        end = start + bytes_per_recs
        body = payload[start:end]

        # Zero-pad to full size if requested and needed
        if zero_pad_to_full and len(body) < FULL_UNITARY_BYTES:
            body = body + b"\x00" * (FULL_UNITARY_BYTES - len(body))

        # Compute timing for this RECS
        recs_atow_decisec = header.atow + idx * header.rtba

        # Handle week rollover
        recs_wn = header.wn
        while recs_atow_decisec >= WEEK_DECISECONDS:
            recs_atow_decisec -= WEEK_DECISECONDS
            recs_wn += 1

        # Convert to GST seconds for filename generation
        recs_gst_sec = week_tow_to_gst_seconds(recs_wn, recs_atow_decisec / 10.0)

        # Generate filename
        recs_filename = generate_recs_filename(
            gst_sec=recs_gst_sec,
            svid=header.svid,
            kdi=header.kdi,
            provider=provider4,
            interface_version=intv_str,
            rand=header.rand,
            file_version=header.file_version,
        )

        # Build 16-byte unitary header
        unitary_hdr = build_unitary_header(
            provider=provider4,
            intv=header.interface_version,
            wn=recs_wn,
            atow_decisec=recs_atow_decisec,
            svid=header.svid,
            kdi=header.kdi,
            rand_flag=header.rand,
            file_version=header.file_version,
        )

        # Write unitary file
        out_path = out_dir / recs_filename
        out_path.write_bytes(unitary_hdr + body)

        _debug(
            f"  [{idx + 1}/{n_recs}] {recs_filename}  "
            f"(WN={recs_wn}, ATOW={recs_atow_decisec} dsec, "
            f"body={len(body)} bytes)"
        )

        unitary_files.append({
            "path": str(out_path),
            "filename": recs_filename,
            "index": idx,
            "svid": header.svid,
            "wn": recs_wn,
            "atow_decisec": recs_atow_decisec,
            "gst_seconds": recs_gst_sec,
            "body_bytes": len(body),
        })

    return {
        "source_path": str(source),
        "header": header._asdict(),
        "n_recs": n_recs,
        "nchip_code": header.nchip,
        "chips_per_recs": chips,
        "bytes_per_recs_raw": bytes_per_recs,
        "unitary_files": unitary_files,
    }


def parse_recs_file_to_binary(
    source: Path,
    *,
    output_directory: Optional[Path] = None,
    force_format: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse a RECS file and extract binaries.

    For aggregated ``.bin`` files this delegates to :func:`parse_aggregated_recs`
    which splits the concatenated payload into individual unitary ``.RCS`` files.

    For old ``.RCS`` files, the 16-byte header is stripped and the raw payload
    is saved as a ``.bin`` file (legacy behaviour).

    Args:
        source: Path to the RECS file.
        output_directory: Directory to save output. Uses source directory if None.
        force_format: Force a specific format ('old' or 'bin'). Auto-detects if None.

    Returns:
        Dict with parsing results.
    """
    _debug(f"Parsing RECS file: {source}")

    if force_format:
        format_name = force_format
    else:
        format_name, _ = detect_recs_format(source)

    # ── Aggregated .bin → split into unitary .RCS ──
    if format_name == "bin":
        return parse_aggregated_recs(
            source, output_directory=output_directory
        )

    # ── Legacy .RCS → strip header, save payload ──
    raw_bytes = source.read_bytes()
    header_size = RECS_HEADER_SIZE_OLD
    _debug(f"  Format: old (unitary), Header size: {header_size} bytes")

    if len(raw_bytes) < header_size:
        raise ParseError(
            f"RECS file '{source}' is too small for header "
            f"({len(raw_bytes)} < {header_size})"
        )

    payload = raw_bytes[header_size:]

    target_dir = (output_directory or source.parent)
    target_dir.mkdir(parents=True, exist_ok=True)
    binary_payload_path = target_dir / f"{source.stem}.bin"

    _debug(f"  Exporting raw binary payload to: {binary_payload_path}")
    binary_payload_path.write_bytes(payload)

    return {
        "source_path": str(source),
        "format": "old",
        "header_size": header_size,
        "total_size": len(raw_bytes),
        "payload_size": len(payload),
        "binary_payload_path": str(binary_payload_path),
        # Legacy files produce a single unitary file (themselves)
        "unitary_files": [
            {
                "path": str(source),
                "filename": source.name,
                "index": 0,
                "body_bytes": len(payload),
            }
        ],
    }


def parse_online_recs_file(
    source: Path,
    *,
    output_directory: Optional[Path] = None,
) -> Dict[str, Any]:
    """Parse an online RECS .bin file (25-byte header) into unitary .RCS files.

    Convenience wrapper around :func:`parse_recs_file_to_binary` with
    ``force_format='bin'``.
    """
    return parse_recs_file_to_binary(
        source, output_directory=output_directory, force_format="bin"
    )


############################################### PIPELINE EXECUTION ##############################################

def _run_pipeline_legacy(
    config: Optional[Mapping[str, object]] = None,
    *,
    recs_enabled: bool = True,
    bgd_enabled: bool = True,
    recs_files: Optional[List[str]] = None,
    logger=None,
    **kwargs,
) -> Dict[str, Any]:
    """Run the parser pipeline.

    For each aggregated ``.bin`` file the parser:
      1. Reads the 25-byte header.
      2. Splits the body into N individual RECS based on NChip (Table 9).
      3. Writes each as a unitary ``.RCS`` file with a 16-byte header and
         proper ``XXXXII_YYDDDHHMMSSS_ss_K_R_VV.RCS`` naming.
      4. Stores the resulting file list in :class:`ExecutionContext` so the
         decryption block can pick them up.

    Legacy ``.RCS`` files are passed through unchanged (only header-stripped
    payloads are written).

    Args:
        config: Optional configuration mapping to override JSON config.
        recs_enabled: Whether RECS parsing is enabled (from SASpipeline).
        bgd_enabled: Whether BGD parsing is enabled (from SASpipeline).
        recs_files: List of RECS file paths (from SASpipeline via ExecutionContext).
        logger: Optional callback for GUI-visible logging.

    Returns:
        Dictionary with parsing results.
    """
    def _log(msg: str) -> None:
        """Log to both console and GUI (if logger provided)."""
        _debug(msg)
        if logger:
            logger(msg)

    # Load config from own JSON, then apply overrides
    base_config = load_config()
    if config:
        base_config.update(config)
    config_map = base_config

    # Get RECS files from parameter or config (only if enabled)
    recs_candidates = None
    if recs_enabled:
        recs_candidates = recs_files or config_map.get("recs_files")
        if recs_candidates:
            _log(f"Processing {len(recs_candidates)} aggregated RECS file(s)")
        else:
            _log("No RECS files to parse")
    else:
        _log("RECS parsing disabled")

    output_directory = config_map.get("output_directory")

    # Resolve output directory — prefer execution-specific location
    output_dir_path = None
    if output_directory:
        output_dir_candidate = Path(str(output_directory)).expanduser()
        if not output_dir_candidate.is_absolute():
            output_dir_candidate = (BASE_DIR / output_dir_candidate).resolve()
        output_dir_path = output_dir_candidate
    else:
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        if ctx.get_execution_id():
            output_dir_path = ctx.get_execution_results_dir("RECS_parsed")
        else:
            output_dir_path = DEFAULT_OUTPUT_DIR

    if output_dir_path:
        output_dir_path.mkdir(parents=True, exist_ok=True)

    # ── Parse RECS files ────────────────────────────────────────────────
    recs_results: List[Dict[str, Any]] = []
    all_unitary_paths: List[str] = []

    if recs_enabled and recs_candidates:
        recs_paths = _iter_recs_files(recs_candidates)
        for recs_path in recs_paths:
            try:
                _log(f"Parsing: {recs_path.name}")
                result = parse_recs_file_to_binary(
                    recs_path,
                    output_directory=output_dir_path,
                )
                recs_results.append(result)

                # Collect unitary file paths for downstream blocks
                n_unitary = 0
                for uf in result.get("unitary_files", []):
                    path = uf.get("path")
                    if path:
                        all_unitary_paths.append(path)
                        n_unitary += 1

                nchip_code = result.get("nchip_code", "?")
                chips = result.get("chips_per_recs", "?")
                _log(
                    f"  → {n_unitary} unitary RECS "
                    f"(NChip={nchip_code}, {chips} chips/RECS)"
                )

            except Exception as exc:
                _log(f"ERROR parsing '{recs_path.name}': {exc}")
                raise ParseError(
                    f"Failed to parse RECS file '{recs_path}': {exc}"
                ) from exc

    # Store parsed unitary files in ExecutionContext for downstream blocks
    if all_unitary_paths:
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        ctx.set("parsed_recs_files", all_unitary_paths)

    # ── BGD parsing (only if enabled) ───────────────────────────────────
    bgd_results: List[str] = []
    if bgd_enabled:
        bgd_paths = config_map.get("bgd_files", [])
        bgd_results = [str(path) for path in bgd_paths] if bgd_paths else []

    total_unitary = sum(r.get("n_recs", 1) for r in recs_results)
    _log(
        f"Done: {len(recs_results)} aggregated → "
        f"{total_unitary} unitary RECS file(s)"
    )

    return {
        "recs": recs_results,
        "parsed_recs_files": all_unitary_paths,
        "bgd_parsed_files": bgd_results,
        "output_directory": str(output_dir_path) if output_dir_path else None,
    }


def run_pipeline(
    config: Optional[Mapping[str, object]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: recs_files (svid_encrypted/bgd_file_enabled come from globals)
    produces: parsed_recs_files
    """
    inputs = dict(inputs or {})
    g = dict(globals or {})
    svid_encrypted = (g.get("svid_encrypted") or "").strip()
    raw = _run_pipeline_legacy(
        config=config,
        recs_enabled=bool(svid_encrypted),
        bgd_enabled=bool(g.get("bgd_file_enabled")),
        recs_files=inputs.get("recs_files"),
        logger=logger,
    )
    return {
        "outputs": {
            "parsed_recs_files": list(raw.get("parsed_recs_files") or []),
        },
        "summary": {
            "aggregated_count": len(raw.get("recs", [])),
            "unitary_count": len(raw.get("parsed_recs_files") or []),
            "bgd_count": len(raw.get("bgd_parsed_files") or []),
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


def run_pipeline_from_json(payload: Union[str, Mapping[str, object]]) -> Dict[str, Any]:
    """Execute the parser pipeline from a JSON-compatible payload."""
    if isinstance(payload, str):
        try:
            config_map = json.loads(payload.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload for parser pipeline") from exc
    elif isinstance(payload, Mapping):
        config_map = payload
    else:
        raise TypeError("Parser payload must be a mapping or JSON string")

    result = run_pipeline(config_map)
    return serialise_value(result)


################################################ EXPORTS ##############################################

__all__ = [
    "load_schema",
    "load_config",
    "save_config",
    "run_pipeline",
    "run_pipeline_from_json",
    "parse_recs_file_to_binary",
    "parse_aggregated_recs",
    "parse_online_recs_file",
    "detect_recs_format",
    "build_unitary_header",
    "nchip_to_chips",
    "nchip_to_bytes",
    "ParseError",
    "NCHIP_TABLE",
    "RECS_HEADER_SIZE_OLD",
    "RECS_HEADER_SIZE_BIN",
    "FULL_UNITARY_CHIPS",
    "FULL_UNITARY_BYTES",
]
