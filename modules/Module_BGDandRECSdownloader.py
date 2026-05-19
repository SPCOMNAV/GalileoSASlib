from __future__ import annotations

import io
import json
import logging
import re
import struct
import tarfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Mapping, NamedTuple, Optional, Tuple, Union

try:
    import requests
except ImportError:
    requests = None

try:
    import paramiko
except ImportError:
    paramiko = None

import socket
import threading
import select
import time

from utils.utils import (
    load_json_file,
    save_json_file,
    console_log,
    BASE_DIR,
    SCHEMA_DIR,
    CONFIG_DIR,
    DATA_DIR,
    GST_EPOCH,
    GST_WEEK_SECONDS,
    gst_seconds_to_datetime,
    gst_to_recs_timestamp,
    generate_recs_filename,
    datetime_to_gst_seconds,
)
from utils.execution_context import get_execution_context

################################################## CONSTANTS ##########################################################

RECS_DATA_DIR = DATA_DIR / "RECS_Unitary"
RECS_ONLINE_DIR = DATA_DIR / "RECS_Online"
BGD_DATA_DIR = DATA_DIR / "BGD"
CERT_DIR = DATA_DIR / "certs"

DEFAULT_RECS_BASE_URL = "https://gsc-europa.eu/acas/recs"
DEFAULT_BGD_BASE_URL = "https://gsc-europa.eu/acas/bgd"

# SAS Online Server Configuration
SAS_SERVER_BASE_URL = "https://217.111.132.9:50567"
SAS_CERT_ENDPOINT = "/cert"
SAS_RECS_ENDPOINT = "/sas/recs"
SAS_SLOG_ENDPOINT = "/SLOG"

LOGGER = logging.getLogger(__name__)

# Active SSH tunnel instance (module-level for reuse)
_active_tunnel: Optional[Any] = None


def get_downloads_dir(subdir: str = "RECS") -> Path:
    """Get the downloads directory for online mode.
    
    Online mode always stores files under the active execution:
        executions/{id}/downloads/{subdir}/
    
    This function is ONLY used by online-download functions
    (download_recs_online, download_slog).  Manual / local-file mode
    resolves its own directory from the ``recs_directory`` parameter
    supplied by ConfigInit.
    
    Args:
        subdir: Subdirectory name ("RECS", "BGD", "SLOG", "certs")
    
    Returns:
        Path to the downloads directory
    """
    ctx = get_execution_context()
    if ctx.get_execution_id():
        return ctx.get_execution_downloads_dir(subdir)
    # Standalone testing (no pipeline execution context)
    fallback_dir = DATA_DIR / "downloads" / subdir
    fallback_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.warning("No execution context – using fallback %s", fallback_dir)
    return fallback_dir


################################################## SSH TUNNEL MANAGEMENT ##############################################

class SSHTunnelConfig(NamedTuple):
    """SSH tunnel configuration.
    
    Attributes:
        enabled: Whether SSH tunnel is enabled
        ssh_host: SSH server hostname
        ssh_port: SSH server port
        username: SSH username
        password: SSH password
        remote_host: Target server IP (accessible from SSH host)
        remote_port: Target server port
        local_port: Local port for tunnel
    """
    enabled: bool
    ssh_host: str
    ssh_port: int
    username: str
    password: str
    remote_host: str
    remote_port: int
    local_port: int


def get_ssh_config(cfg: Optional[Dict[str, Any]] = None) -> SSHTunnelConfig:
    """Get SSH tunnel configuration from config dict.
    
    Args:
        cfg: Configuration dict. If None, loads from file.
    
    Returns:
        SSHTunnelConfig namedtuple with tunnel settings.
    """
    if cfg is None:
        cfg = load_config() or {}
    
    return SSHTunnelConfig(
        enabled=bool(cfg.get("ssh_enabled", False)),
        ssh_host=cfg.get("ssh_host", "sps1.uab.cat"),
        ssh_port=int(cfg.get("ssh_port", 22)),
        username=cfg.get("ssh_username", ""),
        password=cfg.get("ssh_password", ""),
        remote_host=cfg.get("ssh_remote_host", "217.111.132.9"),
        remote_port=int(cfg.get("ssh_remote_port", 50567)),
        local_port=int(cfg.get("ssh_local_port", 50567)),
    )


class SSHTunnel:
    """SSH tunnel wrapper using paramiko directly.
    
    Compatible with paramiko 4.x which removed DSAKey support.
    Creates a local port forwarding tunnel to a remote server.
    """
    
    def __init__(
        self,
        ssh_host: str,
        ssh_port: int,
        username: str,
        password: str,
        remote_host: str,
        remote_port: int,
        local_port: int,
    ):
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.username = username
        self.password = password
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_port = local_port
        
        self._ssh_client: Optional[Any] = None
        self._server_socket: Optional[socket.socket] = None
        self._threads: List[threading.Thread] = []
        self._running = False
        self._local_bind_port: int = 0
    
    @property
    def is_active(self) -> bool:
        """Check if tunnel is active."""
        return self._running and self._ssh_client is not None
    
    @property
    def local_bind_port(self) -> int:
        """Get the actual local port being used."""
        return self._local_bind_port
    
    def start(self) -> None:
        """Start the SSH tunnel."""
        if paramiko is None:
            raise RuntimeError("paramiko library not available")
        
        # Connect to SSH server
        self._ssh_client = paramiko.SSHClient()
        self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        _debug(f"Connecting to SSH server {self.ssh_host}:{self.ssh_port}...")
        self._ssh_client.connect(
            hostname=self.ssh_host,
            port=self.ssh_port,
            username=self.username,
            password=self.password,
            look_for_keys=False,
            allow_agent=False,
        )
        _debug("SSH connection established")
        
        # Create local server socket for tunnel
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("127.0.0.1", self.local_port))
        self._server_socket.listen(5)
        self._local_bind_port = self._server_socket.getsockname()[1]
        
        self._running = True
        
        # Start accept thread
        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()
        self._threads.append(accept_thread)
        
        _debug(f"SSH tunnel listening on localhost:{self._local_bind_port}")
    
    def _accept_loop(self) -> None:
        """Accept incoming connections and create port forward channels."""
        while self._running:
            try:
                self._server_socket.settimeout(1.0)
                try:
                    client_socket, addr = self._server_socket.accept()
                except socket.timeout:
                    continue
                
                if not self._running:
                    client_socket.close()
                    break
                
                _debug(f"Tunnel: new connection from {addr}")
                
                # Open channel to remote host
                transport = self._ssh_client.get_transport()
                if transport is None:
                    client_socket.close()
                    continue
                
                channel = transport.open_channel(
                    "direct-tcpip",
                    (self.remote_host, self.remote_port),
                    ("127.0.0.1", self._local_bind_port),
                )
                
                if channel is None:
                    _debug("Tunnel: failed to open channel")
                    client_socket.close()
                    continue
                
                # Start forwarding thread
                forward_thread = threading.Thread(
                    target=self._forward_data,
                    args=(client_socket, channel),
                    daemon=True,
                )
                forward_thread.start()
                self._threads.append(forward_thread)
                
            except Exception as e:
                if self._running:
                    _debug(f"Tunnel accept error: {e}")
    
    def _forward_data(self, local_socket: socket.socket, channel: Any) -> None:
        """Forward data between local socket and SSH channel."""
        try:
            while self._running:
                r, _, _ = select.select([local_socket, channel], [], [], 1.0)
                
                if local_socket in r:
                    data = local_socket.recv(4096)
                    if not data:
                        break
                    channel.send(data)
                
                if channel in r:
                    data = channel.recv(4096)
                    if not data:
                        break
                    local_socket.send(data)
        except Exception as e:
            if self._running:
                _debug(f"Tunnel forward error: {e}")
        finally:
            try:
                channel.close()
            except:
                pass
            try:
                local_socket.close()
            except:
                pass
    
    def stop(self) -> None:
        """Stop the SSH tunnel."""
        self._running = False
        
        if self._server_socket:
            try:
                self._server_socket.close()
            except:
                pass
            self._server_socket = None
        
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except:
                pass
            self._ssh_client = None
        
        _debug("SSH tunnel stopped")


def start_ssh_tunnel(config: SSHTunnelConfig) -> Optional[SSHTunnel]:
    """Start SSH tunnel based on configuration.
    
    Creates a tunnel: localhost:local_port -> remote_host:remote_port
    
    Args:
        config: SSH tunnel configuration.
    
    Returns:
        SSHTunnel instance if successful, None if failed.
    """
    global _active_tunnel
    
    if paramiko is None:
        _debug("paramiko library not available. Install with: pip install paramiko")
        return None
    
    if not config.enabled:
        _debug("SSH tunnel disabled in configuration")
        return None
    
    if not config.username:
        _debug("SSH username not configured")
        return None
    
    # Stop existing tunnel if any
    stop_ssh_tunnel()
    
    try:
        _debug(f"Starting SSH tunnel: {config.ssh_host}:{config.ssh_port} -> "
               f"localhost:{config.local_port} -> {config.remote_host}:{config.remote_port}")
        
        tunnel = SSHTunnel(
            ssh_host=config.ssh_host,
            ssh_port=config.ssh_port,
            username=config.username,
            password=config.password,
            remote_host=config.remote_host,
            remote_port=config.remote_port,
            local_port=config.local_port,
        )
        
        tunnel.start()
        
        # Give the accept thread time to start listening
        time.sleep(0.5)
        
        # Verify tunnel is running
        if tunnel.is_active:
            _debug(f"SSH tunnel established on localhost:{tunnel.local_bind_port}")
            _active_tunnel = tunnel
            return tunnel
        else:
            _debug("SSH tunnel failed to start")
            return None
            
    except Exception as e:
        _debug(f"SSH tunnel error: {e}")
        return None


def stop_ssh_tunnel() -> None:
    """Stop the active SSH tunnel if running."""
    global _active_tunnel
    
    if _active_tunnel is not None:
        try:
            if _active_tunnel.is_active:
                _debug("Stopping SSH tunnel...")
                _active_tunnel.stop()
            _active_tunnel = None
        except Exception as e:
            _debug(f"Error stopping SSH tunnel: {e}")
            _active_tunnel = None


def is_tunnel_active() -> bool:
    """Check if SSH tunnel is currently active.
    
    Returns:
        True if tunnel is running, False otherwise.
    """
    return _active_tunnel is not None and _active_tunnel.is_active


def get_tunnel_url(config: SSHTunnelConfig) -> str:
    """Get the URL to use when tunnel is active.
    
    Args:
        config: SSH tunnel configuration.
    
    Returns:
        URL string pointing to localhost tunnel port.
    """
    return f"https://localhost:{config.local_port}"


@contextmanager
def ssh_tunnel_context(cfg: Optional[Dict[str, Any]] = None) -> Generator[Optional[str], None, None]:
    """Context manager for SSH tunnel with automatic cleanup.
    
    Usage:
        with ssh_tunnel_context(config) as server_url:
            if server_url:
                # Use server_url for requests
                response = requests.get(f"{server_url}/endpoint")
    
    Args:
        cfg: Configuration dict. If None, loads from file.
    
    Yields:
        Server URL string (localhost if tunnel active, original if not/disabled).
    """
    if cfg is None:
        cfg = load_config() or {}
    
    ssh_config = get_ssh_config(cfg)
    original_url = cfg.get("online_server_url", SAS_SERVER_BASE_URL)
    
    if not ssh_config.enabled:
        # SSH disabled, use original URL
        yield original_url
        return
    
    tunnel = start_ssh_tunnel(ssh_config)
    
    try:
        if tunnel and tunnel.is_active:
            # Use tunnel URL
            yield get_tunnel_url(ssh_config)
        else:
            # Tunnel failed, try original URL
            _debug("SSH tunnel failed, falling back to direct connection")
            yield original_url
    finally:
        if tunnel:
            stop_ssh_tunnel()


################################################## RECS HEADER FORMAT #################################################

class RECSHeader(NamedTuple):
    """Parsed RECS file header (25 bytes).
    
    Attributes:
        provider: 8-char ASCII provider name
        interface_version: Interface version (uint8)
        reserved: Reserved byte (uint8)
        wn: Week number (uint16)
        atow: ATOW - Adapted Time of Week in deciseconds (uint24 -> 3 bytes)
        duration: Duration in deciseconds (uint24 -> 3 bytes)
        svid: Satellite Vehicle ID (uint8)
        kdi: Key Delay Indicator (uint8)
        rand: Randomization flag (uint8)
        nchip: RECS Sequence duration / Number of chips (uint8)
        rtba: Time Between Authentications in deciseconds (uint16)
        file_version: File version (uint8)
    """
    provider: str
    interface_version: int
    reserved: int
    wn: int
    atow: int
    duration: int
    svid: int
    kdi: int
    rand: int
    nchip: int
    rtba: int
    file_version: int


class RECSSignature(NamedTuple):
    """RECS digital signature file info.
    
    Attributes:
        filename: Name of the signature file
        cert_id: Certificate ID (01, 02, etc)
        algorithm: Signature algorithm (p256, p384, p521)
        signature_bytes: Raw signature data
    """
    filename: str
    cert_id: str
    algorithm: str
    signature_bytes: bytes

################################################# SCHEMA/CONFIG LOADERS ################################################

def load_schema() -> Dict[str, Any]:
    """Load the block schema from JSON."""
    return load_json_file(SCHEMA_DIR / "BGDandRECSdownloader_schema.json")

def load_config() -> Dict[str, Any]:
    """Load current config values."""
    return load_json_file(CONFIG_DIR / "BGDandRECSdownloader.json")

############################################### HELPERS ################################################################

def _debug(msg: str) -> None:
    """Print debug message with block name prefix.
    
    Args:
        msg: Debug message to print.
    """
    console_log("BGDandRECSDownloader", msg)


############################################### SAS ONLINE DOWNLOAD ###################################################

def download_sas_certificate(
    server_url: str = SAS_SERVER_BASE_URL,
    output_dir: Optional[Path] = None,
    timeout: int = 30,
) -> Optional[Path]:
    """Download HTTPS certificate from SAS server.
    
    The certificate is needed for secure communication with the SAS server.
    Equivalent to: curl -k https://217.111.132.9:50567/cert -o cert.pem
    
    Args:
        server_url: Base URL of the SAS server.
        output_dir: Directory to save cert.pem. Uses CERT_DIR if None.
        timeout: Request timeout in seconds.
    
    Returns:
        Path to downloaded certificate, or None if download failed.
    """
    if requests is None:
        _debug("requests library not available")
        return None
    
    out_dir = output_dir or CERT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    
    cert_url = f"{server_url.rstrip('/')}{SAS_CERT_ENDPOINT}"
    cert_path = out_dir / "cert.pem"
    
    try:
        _debug(f"Downloading certificate from {cert_url}")
        # Use verify=False because we're downloading the cert itself
        response = requests.get(cert_url, timeout=timeout, verify=False)
        response.raise_for_status()
        
        cert_path.write_bytes(response.content)
        _debug(f"Certificate saved to: {cert_path}")
        return cert_path
        
    except Exception as e:
        _debug(f"Certificate download failed: {e}")
        return None


def get_sas_certificate_path() -> Optional[Path]:
    """Get path to local SAS certificate if it exists.
    
    Returns:
        Path to cert.pem or None if not found.
    """
    cert_path = CERT_DIR / "cert.pem"
    return cert_path if cert_path.exists() else None


def parse_recs_header(data: bytes) -> Optional[RECSHeader]:
    """Parse RECS file 25-byte header.
    
    Header structure (25 bytes total):
    - Provider: ASCII, 8 bytes (64 bits)
    - IntV: uint8, 1 byte
    - Reserved: uint8, 1 byte
    - tSTART WN: uint16, 2 bytes (big-endian)
    - tSTART ATOW: uint24, 3 bytes (deciseconds, big-endian)
    - tDur: uint24, 3 bytes (deciseconds, big-endian)
    - SVID: uint8, 1 byte
    - KDI: uint8, 1 byte
    - RAND: uint8, 1 byte
    - NChip: uint8, 1 byte
    - RTBA: uint16, 2 bytes (deciseconds, big-endian)
    - File Version: uint8, 1 byte
    
    Args:
        data: Raw bytes from RECS file (at least 25 bytes).
    
    Returns:
        RECSHeader namedtuple with parsed fields, or None if parsing failed.
    """
    if len(data) < 25:
        _debug(f"RECS header too short: {len(data)} bytes (need 25)")
        return None
    
    try:
        # Provider: 8 bytes ASCII
        provider = data[0:8].decode('ascii').rstrip('\x00')
        
        # IntV: 1 byte
        interface_version = data[8]
        
        # Reserved: 1 byte
        reserved = data[9]
        
        # WN: 2 bytes big-endian
        wn = struct.unpack('>H', data[10:12])[0]
        
        # ATOW: 3 bytes big-endian (deciseconds)
        atow = (data[12] << 16) | (data[13] << 8) | data[14]
        
        # Duration: 3 bytes big-endian (deciseconds)
        duration = (data[15] << 16) | (data[16] << 8) | data[17]
        
        # SVID: 1 byte
        svid = data[18]
        
        # KDI: 1 byte
        kdi = data[19]
        
        # RAND: 1 byte
        rand_flag = data[20]
        
        # NChip: 1 byte
        nchip = data[21]
        
        # RTBA: 2 bytes big-endian (deciseconds)
        rtba = struct.unpack('>H', data[22:24])[0]
        
        # File Version: 1 byte
        file_version = data[24]
        
        return RECSHeader(
            provider=provider,
            interface_version=interface_version,
            reserved=reserved,
            wn=wn,
            atow=atow,
            duration=duration,
            svid=svid,
            kdi=kdi,
            rand=rand_flag,
            nchip=nchip,
            rtba=rtba,
            file_version=file_version,
        )
        
    except Exception as e:
        _debug(f"Failed to parse RECS header: {e}")
        return None


def parse_signature_filename(filename: str) -> Optional[RECSSignature]:
    """Parse signature filename to extract certificate ID and algorithm.
    
    Signature filename format: <file>_KN.pXXX
    - <file>: Name of the file being signed (without .bin extension)
    - KN: Certificate ID (01, 02, etc.)
    - pXXX: Signature algorithm (p256, p384, p521)
    
    Args:
        filename: Signature filename (e.g., "01_253540000000_000600_00300_14_0_0_3_01_01.p256")
    
    Returns:
        RECSSignature with cert_id and algorithm, or None if parsing failed.
    """
    try:
        basename = Path(filename).stem  # Remove extension
        ext = Path(filename).suffix.lstrip('.')  # Algorithm (p256, p384, p521)
        
        # The cert ID is the last part before the extension
        parts = basename.rsplit('_', 1)
        if len(parts) == 2:
            cert_id = parts[1]
            return RECSSignature(
                filename=filename,
                cert_id=cert_id,
                algorithm=ext,
                signature_bytes=b"",  # Will be populated when reading file
            )
    except Exception as e:
        _debug(f"Failed to parse signature filename '{filename}': {e}")
    
    return None


def build_recs_query_params(
    interface_version: str = "01",
    tstart: str = "",
    duration_sec: int = 300,
    rtba_deciseconds: int = 300,
    svids: List[int] = None,
    kdi: int = 0,
    rand: int = 0,
    nchip: int = 3,
) -> Dict[str, str]:
    """Build query parameters for SAS RECS download request.
    
    URL format: /sas/recs?intv=01&tstart=253540000000&tdur=000300&rtba=00300&svids=14+18&kdi=0&rand=0&nchip=3
    
    Args:
        interface_version: Interface version (e.g., "01")
        tstart: Start time in YYDDDHHMMSSS format (12 chars)
        duration_sec: Duration in seconds
        rtba_deciseconds: Time between authentications in deciseconds
        svids: List of satellite IDs
        kdi: Key delay indicator (0 or 1)
        rand: Randomization flag (0 or 1)
        nchip: Number of chips / RECS sequence duration
    
    Returns:
        Dict of query parameters for requests.get()
    """
    svid_list = svids or []
    svids_str = "+".join(str(s) for s in svid_list)
    
    return {
        "intv": interface_version,
        "tstart": tstart,
        "tdur": f"{duration_sec:06d}",
        "rtba": f"{rtba_deciseconds:05d}",
        "svids": svids_str,
        "kdi": str(kdi),
        "rand": str(rand),
        "nchip": str(nchip),
    }


def download_recs_online(
    tstart: str,
    duration_sec: int,
    svids: List[int],
    rtba_deciseconds: int = 300,
    interface_version: str = "01",
    kdi: int = 0,
    rand: int = 0,
    nchip: int = 3,
    server_url: str = SAS_SERVER_BASE_URL,
    output_dir: Optional[Path] = None,
    timeout: int = 60,
    cert_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Download RECS from SAS online server.
    
    The server returns a TAR file containing:
    - .bin files: RECS data files with 25-byte header + RECS fragments
    - .pXXX files: Digital signatures (p256, p384, p521)
    
    Args:
        tstart: Start time in YYDDDHHMMSSS format (12 digits)
        duration_sec: Duration in seconds (e.g., 300 for 5 minutes)
        svids: List of satellite IDs (e.g., [14, 18])
        rtba_deciseconds: Time between authentications in deciseconds
        interface_version: Interface version (default "01")
        kdi: Key delay indicator (0 or 1)
        rand: Randomization flag (0 or 1)
        nchip: Number of chips / RECS sequence duration
        server_url: Base URL of SAS server
        output_dir: Directory to extract files. Uses RECS_ONLINE_DIR if None.
        timeout: Request timeout in seconds
        cert_path: Path to server certificate for SSL verification
    
    Returns:
        Dict with:
        - success: bool
        - recs_files: List of extracted RECS .bin file paths
        - signature_files: List of extracted signature file paths
        - headers: Dict mapping filename to parsed RECSHeader
        - output_dir: Path where files were extracted
        - error: Error message if failed
    """
    if requests is None:
        return {
            "success": False,
            "error": "requests library not available",
            "tar_file": None,
            "recs_files": [],
            "signature_files": [],
            "bgd_files": [],
            "slog_files": [],
            "headers": {},
        }
    
    # Use execution-specific directory if available, otherwise fallback
    out_dir = output_dir or get_downloads_dir("RECS")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Resolve the parent downloads directory so we can create BGD/ and SLOG/
    # siblings next to RECS/.
    # out_dir is  …/downloads/RECS  →  downloads_root = …/downloads
    downloads_root = out_dir.parent
    recs_dir = out_dir                                   # …/downloads/RECS
    bgd_dir  = downloads_root / "BGD"                    # …/downloads/BGD
    slog_dir = downloads_root / "SLOG"                   # …/downloads/SLOG
    
    # Build request URL and params
    url = f"{server_url.rstrip('/')}{SAS_RECS_ENDPOINT}"
    params = build_recs_query_params(
        interface_version=interface_version,
        tstart=tstart,
        duration_sec=duration_sec,
        rtba_deciseconds=rtba_deciseconds,
        svids=svids,
        kdi=kdi,
        rand=rand,
        nchip=nchip,
    )
    
    _debug(f"Downloading RECS from {url}")
    _debug(f"Query params: {params}")
    
    # Determine SSL verification
    verify = False  # Default: skip verification for self-signed cert
    if cert_path and cert_path.exists():
        verify = str(cert_path)
        _debug(f"Using certificate: {cert_path}")
    
    try:
        # Make request
        response = requests.get(url, params=params, timeout=timeout, verify=verify)
        response.raise_for_status()
        
        _debug(f"Response status: {response.status_code}, size: {len(response.content)} bytes")
        
        # Save TAR file with meaningful name based on request parameters
        svids_str = "+".join(str(s) for s in svids)
        tar_filename = f"{interface_version}_{tstart}_{duration_sec:06d}_{rtba_deciseconds:05d}_{svids_str}_{kdi}_{rand}_{nchip}.tar"
        tar_path = out_dir / tar_filename
        tar_path.write_bytes(response.content)
        _debug(f"Saved TAR file: {tar_path}")
        
        # Extract TAR file
        recs_files = []
        signature_files = []
        bgd_files = []
        slog_files = []
        headers = {}
        
        tar_data = io.BytesIO(response.content)
        
        try:
            # ── First pass: collect filenames to know which category each
            #    signature (.p256/.p384/.p521) belongs to. ──────────────
            data_basenames: Dict[str, str] = {}   # basename_no_ext → category
            with tarfile.open(fileobj=tar_data, mode='r:*') as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    fname = Path(member.name).name
                    if fname.endswith('.bin'):
                        # strip .bin  →  "01_260492000000_000060_00300_18_0_0_3_01"
                        data_basenames[fname[:-4]] = "recs"
                    elif fname.upper().endswith('.BGD'):
                        data_basenames[fname[:fname.rfind('.')]] = "bgd"
                    elif fname.upper().endswith('.LOG'):
                        data_basenames[fname[:fname.rfind('.')]] = "slog"

            # ── Second pass: extract and route to the right directory ──
            tar_data.seek(0)
            with tarfile.open(fileobj=tar_data, mode='r:*') as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    
                    filename = Path(member.name).name  # Get just the filename
                    _debug(f"Extracting: {filename}")
                    
                    # Extract file content
                    file_obj = tar.extractfile(member)
                    if file_obj is None:
                        continue
                    
                    file_content = file_obj.read()
                    
                    # ── Determine destination directory ──────────────────
                    is_signature = any(filename.endswith(ext) for ext in ['.p256', '.p384', '.p521'])
                    
                    if filename.endswith('.bin'):
                        dest_dir = recs_dir
                    elif filename.upper().endswith('.BGD'):
                        dest_dir = bgd_dir
                    elif filename.upper().endswith('.LOG'):
                        dest_dir = slog_dir
                    elif is_signature:
                        # Route signature next to its data file.
                        # Signature name derives from data name, e.g.:
                        #   01_..._01.bin  →  01_..._01_01.p256
                        #   GSCX...01.BGD  →  GSCX...01_01.p256
                        # Walk the known basenames to find a prefix match.
                        dest_dir = recs_dir  # default
                        sig_base = filename[:filename.rfind('.')]  # drop .p256
                        for base, cat in data_basenames.items():
                            if sig_base.startswith(base):
                                dest_dir = {"recs": recs_dir, "bgd": bgd_dir, "slog": slog_dir}[cat]
                                break
                    else:
                        dest_dir = recs_dir  # fallback
                    
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    output_path = dest_dir / filename
                    output_path.write_bytes(file_content)
                    
                    # Categorize files for the return dict
                    if filename.endswith('.bin'):
                        recs_files.append(str(output_path))
                        
                        # Parse header
                        header = parse_recs_header(file_content)
                        if header:
                            headers[filename] = header._asdict()
                            _debug(f"  Header: SVID={header.svid}, WN={header.wn}, "
                                   f"ATOW={header.atow}, dur={header.duration}")
                    
                    elif is_signature:
                        signature_files.append(str(output_path))
                        sig_info = parse_signature_filename(filename)
                        if sig_info:
                            _debug(f"  Signature: cert_id={sig_info.cert_id}, algo={sig_info.algorithm}")
                    
                    elif filename.upper().endswith('.BGD'):
                        bgd_files.append(str(output_path))
                        _debug(f"  BGD file: {filename}")
                    
                    elif filename.upper().endswith('.LOG'):
                        slog_files.append(str(output_path))
                        _debug(f"  SLOG file: {filename}")
                    
                    else:
                        _debug(f"  Unknown file type: {filename}")
        
        except tarfile.TarError as e:
            _debug(f"Failed to extract TAR: {e}")
            # Maybe it's not a TAR? Save raw response
            raw_path = out_dir / f"recs_response_{tstart}.bin"
            raw_path.write_bytes(response.content)
            return {
                "success": False,
                "error": f"Failed to extract TAR: {e}",
                "tar_file": str(tar_path),
                "raw_response_path": str(raw_path),
                "recs_files": [],
                "signature_files": [],
                "bgd_files": [],
                "slog_files": [],
                "headers": {},
            }
        
        _debug(f"Downloaded {len(recs_files)} RECS files, {len(signature_files)} signatures, "
               f"{len(bgd_files)} BGD files, {len(slog_files)} SLOG files")
        
        return {
            "success": True,
            "tar_file": str(tar_path),
            "recs_files": recs_files,
            "signature_files": signature_files,
            "bgd_files": bgd_files,
            "slog_files": slog_files,
            "headers": headers,
            "output_dir": str(out_dir),
            "request_params": params,
        }
        
    except requests.exceptions.RequestException as e:
        _debug(f"RECS download failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "tar_file": None,
            "recs_files": [],
            "signature_files": [],
            "bgd_files": [],
            "slog_files": [],
            "headers": {},
        }


def download_slog(
    provider: str = "GSCX",
    year: int = 25,
    filename: str = "",
    server_url: str = SAS_SERVER_BASE_URL,
    output_dir: Optional[Path] = None,
    timeout: int = 30,
    cert_path: Optional[Path] = None,
) -> Optional[Path]:
    """Download SLOG file from SAS server.
    
    URL format: /SLOG/{PROVIDER}/{YEAR}/{FILENAME}
    Example: /SLOG/GSCX/25/GSCX01_25360.LOG
    
    Args:
        provider: Provider code (e.g., "GSCX")
        year: 2-digit year (e.g., 25 for 2025)
        filename: LOG filename (e.g., "GSCX01_25360.LOG")
        server_url: Base URL of SAS server
        output_dir: Directory to save file. Uses DATA_DIR/SLOG if None.
        timeout: Request timeout in seconds
        cert_path: Path to server certificate
    
    Returns:
        Path to downloaded file, or None if failed.
    """
    if requests is None:
        _debug("requests library not available")
        return None
    
    # Use execution-specific directory if available
    out_dir = output_dir or get_downloads_dir("SLOG")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    url = f"{server_url.rstrip('/')}{SAS_SLOG_ENDPOINT}/{provider}/{year:02d}/{filename}"
    output_path = out_dir / filename
    
    verify = False
    if cert_path and cert_path.exists():
        verify = str(cert_path)
    
    try:
        _debug(f"Downloading SLOG from {url}")
        response = requests.get(url, timeout=timeout, verify=verify)
        response.raise_for_status()
        
        output_path.write_bytes(response.content)
        _debug(f"SLOG saved to: {output_path}")
        return output_path
        
    except Exception as e:
        _debug(f"SLOG download failed: {e}")
        return None


################################################### EXISTING FUNCTIONS ################################################

def generate_expected_recs_list(
    svid_list: List[int],
    gst_start: float,
    duration_sec: float,
    periodicity_sec: float,
    kdi: int,
) -> List[Tuple[str, float, int]]:
    """Generate list of expected RECS filenames for the authentication period.
    
    Args:
        svid_list: List of SVIDs (PRNs) to search.
        gst_start: GST start time in seconds.
        duration_sec: Total duration of authentication period.
        periodicity_sec: Interval between RECS files.
        kdi: Key Delay Indicator to use.
    
    Returns:
        List of tuples (filename, gst_time, svid) for each expected RECS file.
    """
    expected = []
    
    # Calculate number of time slots
    num_slots = max(1, int(duration_sec / periodicity_sec))
    
    for i in range(num_slots):
        gst_time = gst_start + (i * periodicity_sec)
        for svid in svid_list:
            filename = generate_recs_filename(gst_time, svid, kdi)
            expected.append((filename, gst_time, svid))
    
    return expected

#################################################### DOWNLOAD/LOGIC ####################################################

def download_recs_file(
    filename: str,
    base_url: str,
    output_dir: Path,
    timeout: int = 30,
    verify_ssl: bool = True,
) -> Optional[Path]:
    """Download a single RECS file from server.
    
    Args:
        filename: Name of the RECS file to download.
        base_url: Base URL of the RECS server.
        output_dir: Directory to save the file.
        timeout: Request timeout in seconds.
        verify_ssl: Whether to verify SSL certificates.
    
    Returns:
        Path to downloaded file, or None if download failed.
    """
    if requests is None:
        _debug("requests library not available")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    url = f"{base_url.rstrip('/')}/{filename}"
    output_path = output_dir / filename
    
    try:
        _debug(f"Downloading {filename} from {url}")
        response = requests.get(url, timeout=timeout, verify=verify_ssl)
        response.raise_for_status()
        
        output_path.write_bytes(response.content)
        _debug(f"Downloaded: {output_path}")
        return output_path
        
    except Exception as e:
        _debug(f"Download failed for {filename}: {e}")
        return None


def find_or_download_recs(
    expected_files: List[Tuple[str, float, int]],
    recs_directory: Path,
    base_url: str,
    timeout: int = 30,
    verify_ssl: bool = True,
) -> Dict[str, Any]:
    """Find local RECS files or download missing ones.
    
    Args:
        expected_files: List of (filename, gst_time, svid) tuples.
        recs_directory: Directory to search/save RECS files.
        base_url: Base URL for downloading missing files.
        timeout: Request timeout.
        verify_ssl: Whether to verify SSL.
    
    Returns:
        Dict with found files, missing files, and download results.
    """
    recs_directory.mkdir(parents=True, exist_ok=True)
    
    found_files = []
    missing_files = []
    downloaded_files = []
    failed_downloads = []
    
    for filename, gst_time, svid in expected_files:
        local_path = recs_directory / filename
        
        if local_path.exists():
            _debug(f"Found local: {filename}")
            found_files.append({
                "path": str(local_path),
                "filename": filename,
                "gst_time": gst_time,
                "svid": svid,
                "source": "local",
            })
        else:
            missing_files.append((filename, gst_time, svid))
    
    # Try to download missing files
    for filename, gst_time, svid in missing_files:
        downloaded_path = download_recs_file(
            filename, base_url, recs_directory, timeout, verify_ssl
        )
        if downloaded_path:
            downloaded_files.append({
                "path": str(downloaded_path),
                "filename": filename,
                "gst_time": gst_time,
                "svid": svid,
                "source": "download",
            })
        else:
            failed_downloads.append({
                "filename": filename,
                "gst_time": gst_time,
                "svid": svid,
            })
    
    all_files = found_files + downloaded_files
    
    return {
        "success": len(failed_downloads) == 0,
        "files": [f["path"] for f in all_files],
        "file_details": all_files,
        "local_count": len(found_files),
        "downloaded_count": len(downloaded_files),
        "failed_count": len(failed_downloads),
        "failed": failed_downloads,
        "output_dir": str(recs_directory),
    }


def download_bgd_files(
    base_url: str,
    request_params: Dict[str, Any],
    output_dir: Path,
    timeout: int = 30,
    verify_ssl: bool = True,
) -> Dict[str, Any]:
    """Download BGD files from server."""
    if requests is None:
        return {"success": False, "error": "requests library not available", "files": []}

    output_dir.mkdir(parents=True, exist_ok=True)
    _debug(f"Downloading BGD from {base_url}")

    downloaded_files: List[str] = []
    errors: List[str] = []

    try:
        response = requests.get(
            base_url,
            params=request_params,
            timeout=timeout,
            verify=verify_ssl,
        )
        response.raise_for_status()
        
        _debug(f"BGD download response status: {response.status_code}")
        
    except Exception as e:
        errors.append(str(e))
        _debug(f"BGD download error: {e}")

    return {
        "success": len(errors) == 0,
        "files": downloaded_files,
        "errors": errors,
        "output_dir": str(output_dir),
    }

################################################### LOCAL FILE SCANNING ##################################################

def find_local_bgd_files(directory: Path) -> List[str]:
    """Scan local directory for existing BGD files."""
    if not directory.exists():
        return []
    # Find common BGD file extensions
    bgd_files = []
    for pattern in ["*.xml", "*.bgd", "*.BGD", "*.json"]:
        bgd_files.extend(directory.glob(pattern))
    return [str(f) for f in bgd_files if f.is_file() and not f.name.endswith(":Zone.Identifier")]


################################################### PIPELINE RUNNER #####################################################

def parse_svid_list(svid_str: str) -> List[int]:
    """Parse comma-separated SVID string into list of integers.
    
    Args:
        svid_str: Comma-separated string of SVIDs (e.g., "11,12,36").
    
    Returns:
        List of SVID integers.
    """
    if not svid_str or not svid_str.strip():
        return []
    # Support both comma and plus separators
    svid_str = svid_str.replace('+', ',')
    return [int(s.strip()) for s in svid_str.split(",") if s.strip().isdigit()]


def _run_pipeline_kwargs(
    recs_enabled: bool = True,
    bgd_enabled: bool = False,
    svid_encrypted: str = "",
    gst_start_sec: float = 0.0,
    auth_duration_sec: float = 30.0,
    auth_periodicity_sec: float = 30.0,
    recs_kdi: int = 1,
    recs_directory: Optional[str] = None,
    online_mode: bool = False,
    recs_file_enabled: bool = True,
    bgd_file_enabled: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """Legacy kwargs-based core. Kept verbatim; new run_pipeline (contract 1.0)
    below derives these kwargs from the schema-declared `inputs` dict.
    
    Args:
        recs_enabled: Whether to download/use RECS files (derived from ConfigInit.svid_encrypted)
        bgd_enabled: Whether to download/use BGD files (derived from ConfigInit.bgd_file_enabled)
        svid_encrypted: Comma-separated list of encrypted SVIDs (from ConfigInit)
        gst_start_sec: GST start time in seconds (from ConfigInit)
        auth_duration_sec: Total authentication duration in seconds (from ConfigInit)
        auth_periodicity_sec: Interval between RECS files in seconds (from ConfigInit)
        recs_kdi: Key Delay Indicator to use (from ConfigInit)
        recs_directory: Directory to search/save RECS files (from ConfigInit)
        online_mode: If True, use SAS online server instead of local/GSC
        recs_file_enabled: If False, automatically use online mode for RECS (from ConfigInit)
        bgd_file_enabled: If False, automatically use online mode for BGD (from ConfigInit)
    
    Returns:
        Dict with download results for RECS and/or BGD
    """
    # Load block-specific config (URLs, timeouts, etc.)
    cfg = load_config() or {}
    
    # Check if online mode is enabled - automatically enable if file_enabled is False
    # recs_file_enabled=False means: use SAS server instead of local files
    recs_online = (not recs_file_enabled) or online_mode
    bgd_online = (not bgd_file_enabled) or online_mode
    
    _debug(f"Pipeline flags: recs_enabled={recs_enabled}, bgd_enabled={bgd_enabled}, "
           f"recs_online={recs_online}, bgd_online={bgd_online}")
    _debug(f"File flags: recs_file_enabled={recs_file_enabled}, bgd_file_enabled={bgd_file_enabled}")
    _debug(f"RECS params: svid_encrypted='{svid_encrypted}', gst_start={gst_start_sec}, "
           f"duration={auth_duration_sec}, periodicity={auth_periodicity_sec}, kdi={recs_kdi}")
    
    # Show what will happen
    if recs_enabled:
        if recs_online:
            _debug("Mode: RECS will be downloaded from SAS online server")
        else:
            _debug("Mode: RECS will be read from local files or GSC")
    else:
        _debug("Mode: RECS download disabled (no svid_encrypted or recs_svids configured)")

    results = {
        "executed_at": datetime.now(timezone.utc).isoformat() + "Z",
        "recs": None,
        "bgd": None,
        "recs_online": recs_online,
        "bgd_online": bgd_online,
        "params": {
            "recs_enabled": recs_enabled,
            "bgd_enabled": bgd_enabled,
            "svid_encrypted": svid_encrypted,
            "gst_start_sec": gst_start_sec,
            "auth_duration_sec": auth_duration_sec,
            "auth_periodicity_sec": auth_periodicity_sec,
            "recs_kdi": recs_kdi,
        }
    }

    # RECS handling
    if recs_enabled:
        svid_list = parse_svid_list(svid_encrypted)
        
        # Fallback: if svid_encrypted is empty but online mode, use recs_svids from config
        if not svid_list and recs_online:
            svids_from_config = cfg.get("recs_svids", "")
            if svids_from_config:
                svid_list = parse_svid_list(svids_from_config)
                _debug(f"Using recs_svids from block config: {svids_from_config} -> {svid_list}")
        
        if not svid_list:
            _debug("No encrypted SVIDs specified, skipping RECS")
            results["recs"] = {
                "success": True,
                "files": [],
                "message": "No encrypted SVIDs specified",
            }
        elif recs_online:
            # Use online download mode
            _debug("Using SAS online download mode")
            
            # Convert GST to tstart if available, or use config tstart
            if gst_start_sec > 0:
                tstart = gst_to_recs_timestamp(gst_start_sec)
            else:
                tstart = cfg.get("recs_tstart", "")
            
            if not tstart:
                results["recs"] = {
                    "success": False,
                    "files": [],
                    "error": "No start time available (need gst_start_sec or recs_tstart in config)",
                }
            else:
                # Use SSH tunnel context if enabled
                with ssh_tunnel_context(cfg) as server_url:
                    results["recs"] = download_recs_online(
                        tstart=tstart,
                        duration_sec=int(auth_duration_sec) if auth_duration_sec > 0 else int(cfg.get("recs_duration_sec", 120)),
                        svids=svid_list,
                        rtba_deciseconds=int(cfg.get("recs_rtba_deciseconds", 300)),
                        interface_version=cfg.get("recs_interface_version", "01"),
                        kdi=recs_kdi,
                        rand=int(cfg.get("recs_rand", 0)),
                        nchip=int(cfg.get("recs_nchip", 3)),
                        server_url=server_url,
                        timeout=int(cfg.get("online_timeout", 60)),
                        cert_path=get_sas_certificate_path() if cfg.get("online_use_cert", False) else None,
                    )
                # Map recs_files to files for compatibility
                if "recs_files" in results["recs"]:
                    results["recs"]["files"] = results["recs"]["recs_files"]
        
        elif gst_start_sec <= 0:
            _debug("GST start time not specified, skipping RECS download")
            results["recs"] = {
                "success": False,
                "files": [],
                "error": "GST start time must be > 0",
            }
        else:
            # Original local/GSC download mode
            # Generate expected RECS filenames
            expected_files = generate_expected_recs_list(
                svid_list=svid_list,
                gst_start=gst_start_sec,
                duration_sec=auth_duration_sec,
                periodicity_sec=auth_periodicity_sec,
                kdi=recs_kdi,
            )
            
            _debug(f"Expected {len(expected_files)} RECS files for {len(svid_list)} SVIDs")
            for fname, gst, svid in expected_files:
                _debug(f"  - {fname} (GST={gst}, SVID={svid})")
            
            # Resolve RECS directory
            if recs_directory:
                recs_dir = Path(recs_directory)
                if not recs_dir.is_absolute():
                    recs_dir = BASE_DIR / recs_dir
            else:
                recs_output_cfg = cfg.get("recs-output-dir")
                if recs_output_cfg:
                    recs_dir = Path(recs_output_cfg)
                    if not recs_dir.is_absolute():
                        recs_dir = BASE_DIR / recs_dir
                else:
                    recs_dir = RECS_DATA_DIR
            
            # Find or download RECS files
            results["recs"] = find_or_download_recs(
                expected_files=expected_files,
                recs_directory=recs_dir,
                base_url=cfg.get("recs-base-url", DEFAULT_RECS_BASE_URL),
                timeout=int(cfg.get("timeout-seconds", 30)),
                verify_ssl=bool(cfg.get("verify-ssl", True)),
            )

    # BGD handling
    if bgd_enabled:
        # Use execution-specific directory for downloads, config for local files
        ctx = get_execution_context()
        if ctx.get_execution_id() and bgd_online:
            bgd_output = ctx.get_execution_downloads_dir("BGD")
        else:
            bgd_output = Path(cfg.get("bgd-output-dir") or str(BGD_DATA_DIR))
            if not bgd_output.is_absolute():
                bgd_output = BASE_DIR / bgd_output

        if bgd_online:
            # BGD online mode - for now still uses GSC download
            # TODO: Implement SAS-specific BGD endpoint if available
            _debug(f"BGD online mode enabled (bgd_file_enabled={bgd_file_enabled})")
            try:
                bgd_request = json.loads(cfg.get("bgd-request-json", "{}"))
            except json.JSONDecodeError:
                bgd_request = {}

            results["bgd"] = download_bgd_files(
                base_url=cfg.get("bgd-base-url", DEFAULT_BGD_BASE_URL),
                request_params=bgd_request,
                output_dir=bgd_output,
                timeout=int(cfg.get("timeout-seconds", 30)),
                verify_ssl=bool(cfg.get("verify-ssl", True)),
            )
            results["bgd"]["source"] = "online"
        else:
            # Local file mode - first check for local files
            local_bgd = find_local_bgd_files(bgd_output)
            
            if local_bgd:
                _debug(f"Found {len(local_bgd)} local BGD file(s) in {bgd_output}")
                results["bgd"] = {
                    "success": True,
                    "files": local_bgd,
                    "errors": [],
                    "output_dir": str(bgd_output),
                    "source": "local",
                }
            else:
                _debug(f"No local BGD files found in {bgd_output}, downloading from GSC...")
                try:
                    bgd_request = json.loads(cfg.get("bgd-request-json", "{}"))
                except json.JSONDecodeError:
                    bgd_request = {}

                results["bgd"] = download_bgd_files(
                    base_url=cfg.get("bgd-base-url", DEFAULT_BGD_BASE_URL),
                    request_params=bgd_request,
                    output_dir=bgd_output,
                    timeout=int(cfg.get("timeout-seconds", 30)),
                    verify_ssl=bool(cfg.get("verify-ssl", True)),
                )
                results["bgd"]["source"] = "download"

    return results


############################################ PIPELINE EXECUTION (CONTRACT 1.0) ############################################

def _resolve_gst_start(inputs: Mapping[str, Any], g: Mapping[str, Any]) -> float:
    """Resolve the GST start time used for the RECS request.

    Priority:
      1. ``globals['gst_start_time_sec']`` if > 0  → manual user override (ConfigInit)
      2. ``inputs['snapshot_gst_sec']``            → published by SnapshotRecording
      3. ``inputs['snapshot_path_e6/e1']``         → derive from filename
      4. current UTC                               → live-mode fallback when no snapshot
                                                     is available yet
    """
    gst = float(g.get("gst_start_time_sec") or 0)
    if gst > 0:
        return gst

    snap_gst = inputs.get("snapshot_gst_sec")
    if snap_gst:
        return float(snap_gst)

    is_live = not bool(g.get("rf_input_from_file", True))
    if is_live:
        snap = (inputs.get("snapshot_path_e6") or inputs.get("snapshot_path_e1") or "")
        if snap:
            m = re.search(r'(\d{8})_(\d{6})Z', Path(snap).name)
            if m:
                try:
                    snap_dt = datetime.strptime(
                        m.group(1) + m.group(2), '%Y%m%d%H%M%S'
                    ).replace(tzinfo=timezone.utc)
                    return datetime_to_gst_seconds(snap_dt)
                except ValueError:
                    pass
        return datetime_to_gst_seconds(datetime.now(timezone.utc))

    return 0.0


def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """Schema-driven entry point. See default_json/BGDandRECSdownloader_schema.json
    for the consumes/produces contract."""
    inputs = dict(inputs or {})
    g = dict(globals or {})

    svid_encrypted = (g.get("svid_encrypted") or "").strip()
    recs_file_enabled = bool(g.get("recs_file_enabled", True))
    bgd_file_enabled = bool(g.get("bgd_file_enabled", False))

    # recs_enabled derivation: encrypted SVIDs in ConfigInit OR explicit recs_svids
    # in this block's own config when running purely online.
    recs_enabled = bool(svid_encrypted)
    if not recs_enabled and not recs_file_enabled:
        own_cfg = config or load_config()
        if (own_cfg.get("recs_svids") or "").strip():
            recs_enabled = True

    gst_start_sec = _resolve_gst_start(inputs, g)
    if gst_start_sec > 0:
        logger(f"GST start = {gst_start_sec:.1f} s")

    raw = _run_pipeline_kwargs(
        recs_enabled=recs_enabled,
        bgd_enabled=bgd_file_enabled,
        svid_encrypted=svid_encrypted,
        gst_start_sec=gst_start_sec,
        auth_duration_sec=float(g.get("auth_duration_sec") or 30.0),
        auth_periodicity_sec=float(g.get("auth_periodicity_sec") or 30.0),
        recs_kdi=int(g.get("recs_kdi") or 0),
        recs_directory=g.get("recs_directory"),
        online_mode=False,
        recs_file_enabled=recs_file_enabled,
        bgd_file_enabled=bgd_file_enabled,
    )

    recs_block = raw.get("recs") or {}
    bgd_block = raw.get("bgd") or {}
    recs_files = list(recs_block.get("files") or [])
    bgd_files = list(bgd_block.get("files") or [])

    return {
        "outputs": {
            "recs_files": recs_files,
            "bgd_files": bgd_files,
        },
        "summary": {
            "recs_count": len(recs_files),
            "bgd_count": len(bgd_files),
            "recs_online": raw.get("recs_online"),
            "bgd_online": raw.get("bgd_online"),
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


###################################################### EXPORTS ###########################################################

__all__ = [
    "load_schema",
    "load_config",
    "run_pipeline",
    "download_sas_certificate",
    "download_recs_online",
    "download_slog",
    "parse_recs_header",
    "RECSHeader",
    "RECSSignature",
    "SSHTunnel",
    "SSHTunnelConfig",
    "get_ssh_config",
    "start_ssh_tunnel",
    "stop_ssh_tunnel",
    "is_tunnel_active",
    "ssh_tunnel_context",
    "SAS_SERVER_BASE_URL",
    "RECS_ONLINE_DIR",
]
