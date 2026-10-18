"""DNS input validation shared by dns-api and the tests. Everything here is a trust boundary: the values end up in zone files."""
import ipaddress
import re

LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)\Z")
REC_LABEL = re.compile(r"^(\*|_?[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?)\Z")
TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "SRV", "NS", "PTR")


def normalize_zone_name(name: str) -> str:
    n = (name or "").strip().lower().rstrip(".")
    if not n or len(n) > 253 or "." not in n or not all(LABEL.match(x) for x in n.split(".")):
        raise ValueError("invalid zone name")
    return n


def normalize_name(name: str) -> str:
    """Record name relative to its zone: '@' for the apex, no trailing dot."""
    n = (name or "").strip().lower()
    if n == "@":
        return n
    if not n or len(n) > 253 or not all(REC_LABEL.match(x) for x in n.split(".")):
        raise ValueError("invalid record name")
    return n


def _fqdn(v: str) -> str:
    v = v.strip().lower()
    if not v or len(v) > 254 or not all(LABEL.match(x) for x in v.rstrip(".").split(".")):
        raise ValueError(f"invalid host name: {v[:40]!r}")
    return v if v.endswith(".") else v + "."


def _int(v: str, hi: int) -> int:
    if not v.isdigit() or not 0 <= int(v) <= hi:
        raise ValueError(f"invalid number: {v[:20]!r}")
    return int(v)


def _one(rtype: str, v: str) -> str:
    if rtype == "A":
        return str(ipaddress.IPv4Address(v.strip()))
    if rtype == "AAAA":
        return str(ipaddress.IPv6Address(v.strip()))
    if rtype in ("CNAME", "NS", "PTR"):
        return _fqdn(v)
    if rtype == "MX":
        p = v.split()
        if len(p) != 2:
            raise ValueError("MX needs '<priority> <host>'")
        return f"{_int(p[0], 65535)} {_fqdn(p[1])}"
    if rtype == "SRV":
        p = v.split()
        if len(p) != 4:
            raise ValueError("SRV needs '<priority> <weight> <port> <host>'")
        return f"{_int(p[0], 65535)} {_int(p[1], 65535)} {_int(p[2], 65535)} {_fqdn(p[3])}"
    if rtype == "TXT":
        if not v or len(v) > 4000 or not all(32 <= ord(c) < 127 for c in v):
            raise ValueError("TXT must be 1-4000 printable ASCII characters")
        return v  # quotes, ';' and '\' are escaped by the renderer
    raise ValueError(f"unsupported record type {rtype}")


def check_record(rtype: str, name: str, records: list[str]) -> list[str]:
    """Validated, normalized, de-duplicated record data. Raises ValueError."""
    rtype = rtype.upper()
    if rtype not in TYPES:
        raise ValueError(f"unsupported record type {rtype}")
    if not records:
        raise ValueError("records must not be empty")
    try:
        out = list(dict.fromkeys(_one(rtype, r) for r in records))
    except ipaddress.AddressValueError as exc:
        raise ValueError(str(exc)) from None
    if rtype == "CNAME" and len(out) != 1:
        raise ValueError("a CNAME has exactly one target")
    return out


def check_cname_rules(existing: dict[str, set[str]], name: str, rtype: str, apex: bool) -> None:
    """existing: record name -> types already in the zone (excluding the recordset being replaced)."""
    here = existing.get(name, set())
    if rtype == "CNAME":
        if apex or name == "@":
            raise ValueError("a CNAME cannot sit at the zone apex")
        if here:
            raise ValueError("a CNAME cannot share a name with other records")
    elif "CNAME" in here:
        raise ValueError("this name already has a CNAME")
