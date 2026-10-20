"""Pure self-fencing decision for the HA watchdog (no I/O)."""
from __future__ import annotations


def decide(now: float, last_ok: float, timeout: float, peers_reach: list[bool], has_peers: bool, domains: list[str]) -> str:
    """wait: ha-api heard recently. kill: this node is isolated. hold: silent but killing would be pointless or unsafe.

    peers_reach holds the answers of the peers that replied (True = that peer can reach ha-api).
    """
    if now - last_ok < timeout:
        return "wait"
    if not has_peers or not domains:
        return "hold"
    if not peers_reach or any(peers_reach):
        return "kill"
    return "hold"
