"""Reaching another homeserver's federation API safely (fed_openid.py): the Matrix spec's server
discovery (Server-Server API, "Resolving server names") and one capped HTTPS GET.

  1. an IP literal, or a hostname with a port: used as it is (default port 8448);
  2. else GET https://<hostname>/.well-known/matrix/server ({"m.server": "<delegated>[:port]"}),
     following at most MAX_REDIRECTS https redirects, each hop checked again; a delegated IP or
     port is used as it is, else its SRV record (_matrix-fed._tcp, then _matrix._tcp), else :8448;
  3. no (valid) well-known: the hostname's own SRV records, else <hostname>:8448.

Every connection goes to an address that was checked first (safe_fetch.resolve: every address of
the name public, never this machine, its network, link-local, multicast or reserved, IPv4 or
IPv6) and is pinned to it (no second lookup to rebind). TLS is always verified, for the name the
spec says (the delegated hostname, never an SRV target). Hard deadlines and size caps throughout.
Discovery answers are cached briefly. Callers pass secrets in `path`; nothing here logs a path.
"""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.parse

import fed_dns
import safe_fetch

DEFAULT_PORT = 8448
MAX_REDIRECTS = 3
WELL_KNOWN_MAX = 16_384
CACHE_OK_S, CACHE_FAIL_S = 600, 60
MAX_CACHED = 1000
USER_AGENT = "hagency-rooms (Matrix OpenID check)"
LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
PORT_RE = re.compile(r"^[0-9]{1,5}$")
Refused = safe_fetch.Refused
_cache: dict[str, tuple[float, tuple | None]] = {}   # server name → (expires, target or None)


def parse_server_name(name: object) -> tuple[str, int | None, bool] | None:
    """(host, port or None, is an IP literal) for a server name as the spec's grammar has it
    (hostname [":" port]; an IPv4 literal, "[" IPv6 "]" or a DNS name), else None."""
    if not isinstance(name, str) or not 0 < len(name) <= 255 or not name.isascii():
        return None
    if name.startswith("["):
        host, sep, rest = name[1:].partition("]")
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        if not sep or (rest and not rest.startswith(":")):
            return None
        port = rest[1:] if rest else None
        is_ip = True
    else:
        host, sep, port = name.partition(":")
        port = port if sep else None
        is_ip = _is_ipv4(host)
        if not is_ip and (re.fullmatch(r"[0-9.]+", host) or not all(LABEL_RE.match(p) for p in host.split("."))):
            return None                       # "1.2.3" is neither an address nor a name
    if port is not None and (not PORT_RE.match(port) or not 0 < int(port) < 65536):
        return None
    return host, int(port) if port is not None else None, is_ip


def _is_ipv4(host: str) -> bool:
    try:
        ipaddress.IPv4Address(host)
        return True
    except ValueError:
        return False


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to a pre-checked IP; SNI and the certificate check are for `tls_name`."""
    def __init__(self, tls_name, ip, port, timeout):
        super().__init__(tls_name, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


CONNECT = _Pinned                             # tests replace this with a stand-in


def remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise Refused("took too long")
    return left


def get(host: str, port: int, path: str, host_header: str, tls_name: str, deadline: float,
        cap: int) -> tuple[int, str | None, bytes]:
    """One GET (no redirects followed): (status, Location, body up to `cap` bytes). Raises Refused
    for a non-public address, a body over `cap`, or the deadline passing (also while reading)."""
    ip = safe_fetch.resolve(host, port)       # every address public, else Refused; this one is used
    conn = CONNECT(tls_name, ip, port, remaining(deadline))
    try:
        conn.request("GET", path, headers={"Host": host_header, "User-Agent": USER_AGENT, "Accept": "application/json"})
        resp = conn.getresponse()
        size = resp.getheader("Content-Length")
        if size is not None and (not size.strip().isdigit() or int(size) > cap):
            raise Refused("answer too big")
        body = b""
        while True:
            if conn.sock is not None:         # a slow drip of bytes still ends at the deadline
                conn.sock.settimeout(remaining(deadline))
            chunk = resp.read1(min(65536, cap + 1 - len(body)))
            if not chunk:
                break
            body += chunk
            if len(body) > cap:
                raise Refused("answer too big")
            remaining(deadline)
        return resp.status, resp.getheader("Location"), body
    except (OSError, http.client.HTTPException) as e:
        raise Refused(f"couldn't reach {tls_name}") from e
    finally:
        conn.close()


def well_known(host: str, deadline: float) -> str | None:
    """The delegated server name from https://host/.well-known/matrix/server, or None (any problem:
    the spec then falls back to SRV records). Redirects: https only, each hop checked anew."""
    url = f"https://{host}/.well-known/matrix/server"
    for _ in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        try:
            port = parts.port or 443
        except ValueError:
            return None
        if parts.scheme != "https" or not parts.hostname or parts.username or parse_server_name(parts.hostname) is None:
            return None
        netloc = parts.hostname if port == 443 else f"{parts.hostname}:{port}"
        target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            status, location, body = get(parts.hostname, port, target, netloc, parts.hostname, deadline, WELL_KNOWN_MAX)
        except Refused:
            return None
        if status in (301, 302, 303, 307, 308) and location:
            url = urllib.parse.urljoin(url, location)
            continue
        if status != 200:
            return None
        try:
            data = json.loads(body)
        except ValueError:
            return None
        found = data.get("m.server") if isinstance(data, dict) else None
        return found if parse_server_name(found) is not None else None
    return None


def _by_srv(host: str, host_header: str) -> tuple | None:
    for service in ("_matrix-fed._tcp.", "_matrix._tcp."):
        for target, port in fed_dns.lookup(service + host)[:1]:
            if parse_server_name(target) is not None and not parse_server_name(target)[2]:
                return target, port, host_header, host
    return None


def resolve(server_name: str, deadline: float) -> tuple[str, int, str, str]:
    """(host to connect to, port, Host header, TLS name) for a server name; Refused if it isn't one."""
    parsed = parse_server_name(server_name)
    if parsed is None:
        raise Refused("not a server name")
    host, port, is_ip = parsed
    if is_ip or port is not None:
        return host, port or DEFAULT_PORT, server_name, host
    delegated = well_known(host, deadline)
    if delegated is not None:
        d_host, d_port, d_ip = parse_server_name(delegated)
        if d_ip or d_port is not None:
            return d_host, d_port or DEFAULT_PORT, delegated, d_host
        return _by_srv(d_host, d_host) or (d_host, DEFAULT_PORT, d_host, d_host)
    return _by_srv(host, host) or (host, DEFAULT_PORT, host, host)


def cached_resolve(server_name: str, deadline: float) -> tuple[str, int, str, str]:
    """resolve(), remembered briefly (a failure for less long)."""
    now = time.monotonic()
    hit = _cache.get(server_name)
    if hit and hit[0] > now:
        if hit[1] is None:
            raise Refused("couldn't find that server (recently)")
        return hit[1]
    if len(_cache) >= MAX_CACHED:
        for key in [k for k, (until, _) in _cache.items() if until <= now] or list(_cache)[:MAX_CACHED // 2]:
            _cache.pop(key, None)
    try:
        found = resolve(server_name, deadline)
    except Refused:
        _cache[server_name] = (now + CACHE_FAIL_S, None)
        raise
    _cache[server_name] = (now + CACHE_OK_S, found)
    return found
