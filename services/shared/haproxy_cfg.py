"""Render one load balancer's haproxy.cfg from the desired-state tree (see load_balancer/app/drivers/base.py).

Used by load-balancer-api (to validate input before accepting it) and by lb-worker (to write the file it runs).
Every value that lands in the file goes through _safe(): whitespace or control characters would let a caller
inject extra directives.
"""
from __future__ import annotations

_BALANCE = {"LEAST_CONNECTIONS": "leastconn", "SOURCE_IP": "source"}
_SUFFIX = {"STARTS_WITH": "_beg", "ENDS_WITH": "_end", "EQUAL_TO": "", "CONTAINS": "_sub", "REGEX": "_reg"}

def _acl(r: dict) -> str:
    """haproxy ACL (fetch + value) for one L7 rule."""
    typ, cmp_ = r["type"], r["compare_type"]
    sfx = _SUFFIX.get(cmp_)
    val = _safe(r["value"], "l7 rule value")
    if sfx is None or typ not in ("PATH", "HOST_NAME", "FILE_TYPE", "HEADER", "COOKIE"):
        raise ValueError(f"unsupported l7 rule {typ}/{cmp_}")
    if typ == "PATH":
        return f"path{sfx} {val}"
    if typ == "HOST_NAME":
        return f"hdr{sfx}(host) -i {val}"
    if typ == "FILE_TYPE":
        if cmp_ == "EQUAL_TO":
            return f"path_end -i .{val}"
        if cmp_ == "REGEX":
            return f"path_reg -i [.]({val})$"
        raise ValueError("FILE_TYPE takes EQUAL_TO or REGEX")
    fetch = "hdr" if typ == "HEADER" else "cook"
    return f"{fetch}{sfx}({_safe(r.get('key') or '', 'l7 rule key')}) {val}"

def _safe(value, what: str) -> str:
    s = str(value)
    if not s or any(c.isspace() or not c.isprintable() for c in s):
        raise ValueError(f"unsafe {what} for haproxy config")
    return s


def render(lb: dict, listeners: list[dict], stats_socket: str | None = None) -> str:
    """haproxy keeps members whose health check fails out of rotation itself, so OFFLINE ones stay in the file."""
    out = ["global", "    maxconn 4096"] + ([f"    stats socket {_safe(stats_socket, 'socket')} mode 600 level user"] if stats_socket else []) + ["",
           "defaults", "    timeout connect 5s", "    timeout client 30s", "    timeout server 30s", ""]
    vip = _safe(lb["vip_address"], "vip")
    for ls in listeners:
        lid = _safe(ls["id"], "listener id")
        http = ls["protocol"] in ("HTTP", "HTTPS")
        mode = "http" if http else "tcp"
        bind = f"    bind {vip}:{int(ls['protocol_port'])}"
        if ls["protocol"] == "HTTPS" and ls.get("ssl_cert_path"):
            bind += f" ssl crt {_safe(ls['ssl_cert_path'], 'ssl_cert_path')}"
        out += [f"frontend fe_{lid}", bind, f"    mode {mode}"]
        if http:
            out.append("    option forwardfor")
        default = ls.get("default_pool_id")
        for i, pol in enumerate(sorted(ls.get("policies", []), key=lambda p: p["position"])):
            conds = []
            for j, r in enumerate(pol["rules"]):
                out.append(f"    acl p{i}r{j} {_acl(r)}")
                conds.append(("!" if r.get("invert") else "") + f"p{i}r{j}")
            cond = (" if " + " ".join(conds)) if conds else ""
            if pol["action"] == "REJECT":
                out.append(f"    http-request deny{cond}")
            elif pol["action"] in ("REDIRECT_TO_URL", "REDIRECT_PREFIX"):
                kind, url = ("location", pol.get("redirect_url")) if pol["action"] == "REDIRECT_TO_URL" else ("prefix", pol.get("redirect_prefix"))
                out.append(f"    http-request redirect {kind} {_safe(url, 'redirect target')} code {int(pol.get('redirect_http_code') or 302)}{cond}")
            elif pol.get("redirect_pool_id"):
                out.append(f"    use_backend be_{_safe(pol['redirect_pool_id'], 'pool id')}{cond}")
        if default:
            out.append(f"    default_backend be_{_safe(default, 'pool id')}")
        out.append("")
        for pool in ls["pools"]:
            out += [f"backend be_{_safe(pool['id'], 'pool id')}", f"    mode {mode}",
                    f"    balance {_BALANCE.get(pool['lb_algorithm'], 'roundrobin')}"]
            hm = pool.get("health_monitor")
            check = ""
            if hm:
                if hm["type"] == "HTTP" and http:
                    out.append(f"    option httpchk GET {_safe(hm['url_path'], 'url_path')}")
                check = f" check inter {int(hm['delay'])}s fall {int(hm['max_retries'])}"
            for m in pool["members"]:
                if m["weight"] <= 0:
                    continue
                out.append(f"    server m_{_safe(m['id'], 'member id')} {_safe(m['address'], 'member address')}:"
                           f"{int(m['protocol_port'])} weight {int(m['weight'])}{check}")
            out.append("")
    return "\n".join(out)
