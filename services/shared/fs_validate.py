"""Input checks for file-store shares and access rules."""
import ipaddress


def check_access(to: str, level: str) -> tuple[str, str]:
    """(normalized IPv4 CIDR, level). A rule is never wider than /8: 'allow everyone' is not expressible."""
    if level not in ("rw", "ro"):
        raise ValueError("level must be rw or ro")
    try:
        net = ipaddress.ip_network(str(to).strip(), strict=False)
    except ValueError:
        raise ValueError("'to' must be an IPv4 address or CIDR") from None
    if net.version != 4:
        raise ValueError("only IPv4 is supported")
    if net.prefixlen < 8:
        raise ValueError("network too wide: prefix must be /8 or longer")
    return str(net), level


def check_size(gb: int) -> int:
    if not isinstance(gb, int) or isinstance(gb, bool) or not 1 <= gb <= 1000:
        raise ValueError("size_gb must be between 1 and 1000")
    return gb
