"""
Email link scanners: requests that open our links without a reader behind them.

Mail gateways (Outlook Safe Links, Proofpoint, Mimecast, Barracuda, ISP filters) open every link in an
email seconds after delivery from cloud data centers, often in a real Chrome or Edge, so the user agent
alone (_BOT_UA in src/routes/public.py) misses them. On Senior Daily Benefits they were 27-40% of GA
sessions and cast votes and offer clicks. A reader at home or on a phone is on a residential ISP or a
carrier, never on a cloud provider's published ranges (src/cloud_ranges.txt: Google Cloud, AWS, Azure,
Oracle; refresh monthly with `python tools/update_cloud_ranges.py`).
"""
import bisect
import ipaddress
import os

from flask import request

RANGES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud_ranges.txt")
_ranges = None  # {4: (starts, ends, providers), 6: (...)}


def _load():
    global _ranges
    if _ranges is not None:
        return _ranges
    rows = {4: [], 6: []}
    try:
        with open(RANGES_FILE) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                provider, cidr = line.split()
                n = ipaddress.ip_network(cidr)
                rows[n.version].append((int(n.network_address), int(n.broadcast_address), provider))
    except OSError:
        pass  # no list: nobody is treated as a scanner by address
    _ranges = {}
    for v, r in rows.items():
        r.sort()
        _ranges[v] = ([a for a, _, _ in r], [b for _, b, _ in r], [p for _, _, p in r])
    return _ranges


def cloud_provider(ip: str) -> str:
    """'gcp', 'aws', 'azure' or 'oracle' when ip is in that provider's published ranges, else ''."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return ""
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    starts, ends, providers = _load()[addr.version]
    i = bisect.bisect_right(starts, int(addr)) - 1
    return providers[i] if i >= 0 and int(addr) <= ends[i] else ""


def client_ip() -> str:
    """The reader's address behind Cloudflare and Railway."""
    fwd = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return request.headers.get("CF-Connecting-IP") or fwd or request.remote_addr or ""


def from_cloud() -> bool:
    try:
        return bool(cloud_provider(client_ip()))
    except Exception:  # noqa: BLE001  a lookup problem never costs a reader anything
        return False
