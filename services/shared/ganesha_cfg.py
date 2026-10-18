"""Render an NFS-Ganesha config for one share network (pure; the worker validates and applies it).

Every export is NFSv4, Root_Squash, and denies everyone except the CLIENT blocks built from the access rules.
"""


def render(bind_ip: str, shares: list[dict]) -> str:
    out = [
        "NFS_CORE_PARAM {",
        "    Protocols = 4;",
        "    NFS_Port = 2049;",
        f"    Bind_addr = {bind_ip};",
        "    Enable_NLM = false;",
        "    Enable_RQUOTA = false;",
        "}",
        "NFSV4 {",
        "    Grace_Period = 10;",
        "}",
    ]
    for n, s in enumerate(sorted(shares, key=lambda s: s["id"]), start=1):
        out += [
            "EXPORT {",
            f"    Export_Id = {n};",
            f'    Path = "{s["path"]}";',
            f'    Pseudo = "/{s["id"]}";',
            "    Access_Type = None;",
            "    Squash = Root_Squash;",
            "    SecType = sys;",
            "    FSAL { Name = VFS; }",
        ]
        for r in sorted(s["rules"], key=lambda r: r["to"]):
            out += [
                "    CLIENT {",
                f"        Clients = {r['to']};",
                f"        Access_Type = {'RW' if r['level'] == 'rw' else 'RO'};",
                "    }",
            ]
        out.append("}")
    return "\n".join(out) + "\n"
