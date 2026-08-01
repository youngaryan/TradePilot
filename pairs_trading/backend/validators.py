from __future__ import annotations

import ipaddress
from pathlib import Path, PureWindowsPath
import socket
from urllib.parse import urlparse

from .config import BackendSettings


BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}
BLOCKED_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}


def validate_relative_path(value: str | Path | None, *, settings: BackendSettings, field_name: str) -> None:
    if value is None:
        return
    raw = str(value).strip()
    if not raw:
        return
    path = Path(raw)
    win = PureWindowsPath(raw)
    unsafe = (
        path.is_absolute()
        or win.is_absolute()
        or raw.startswith("\\\\")
        or any(part == ".." for part in path.parts)
        or any(part == ".." for part in win.parts)
    )
    if unsafe and settings.is_production:
        raise ValueError(f"{field_name} must be a tenant-owned artifact ID in production; raw filesystem paths are not accepted.")


def validate_url(value: str, *, settings: BackendSettings, field_name: str = "url") -> None:
    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must use http or https.")
    if settings.is_production and parsed.scheme != "https":
        raise ValueError(f"{field_name} must use HTTPS in production.")
    hostname = (parsed.hostname or "").casefold()
    if not hostname:
        raise ValueError(f"{field_name} must include a hostname.")
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".local"):
        raise ValueError(f"{field_name} points to a blocked local hostname.")
    literal_ip: ipaddress._BaseAddress | None = None
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        _validate_ip(literal_ip, field_name=field_name)
    else:
        try:
            resolved = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(hostname, None)}
        except socket.gaierror:
            resolved = set()
        for address in resolved:
            _validate_ip(address, field_name=field_name)
    if settings.is_production and settings.allowed_web_domains:
        allowed = tuple(domain.casefold() for domain in settings.allowed_web_domains)
        if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed):
            raise ValueError(f"{field_name} host is not in the approved crawl allowlist.")


def _validate_ip(address: ipaddress._BaseAddress, *, field_name: str) -> None:
    if (
        address in BLOCKED_IPS
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise ValueError(f"{field_name} resolves to a blocked network address.")
