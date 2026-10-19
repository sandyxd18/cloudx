"""Reconcile wanted containers against docker's state (pure, idempotent). have: id -> docker state."""


def plan(want: dict[str, dict], have: dict[str, str]) -> list[tuple]:
    acts: list[tuple] = []
    for cid, w in sorted(want.items()):
        if w["want"] == "deleting":
            acts.append(("delete", cid))
        elif cid not in have:
            acts.append(("create", cid))
        elif w["want"] == "restart":
            acts.append(("restart", cid))
        elif w["want"] == "running" and have[cid] != "running":
            acts.append(("start", cid))
        elif w["want"] == "stopped" and have[cid] == "running":
            acts.append(("stop", cid))
    return acts
