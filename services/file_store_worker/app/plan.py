"""Reconcile what the API wants against what this node has (pure, idempotent)."""


def plan(want: dict[str, dict], have: dict[str, dict]) -> list[tuple]:
    acts: list[tuple] = []
    for sid, w in sorted(want.items()):
        if w["status"] == "deleting":
            acts.append(("delete", sid))
        elif sid not in have:
            acts.append(("create", sid))
        elif w["size_gb"] > have[sid]["size_gb"]:
            acts.append(("extend", sid, w["size_gb"]))
    return acts
