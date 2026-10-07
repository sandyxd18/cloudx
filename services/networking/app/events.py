from shared.rpc import RPCClient as _RPCClientClass

from .config import RABBITMQ_URL, log

_rpc = None  # lazily initialised on first event publish

def _publish_net_event(event_type: str, payload: dict) -> None:
    """Best-effort event publishing — never raises, never blocks."""
    global _rpc
    if _RPCClientClass is None:
        return
    try:
        if _rpc is None:
            _rpc = _RPCClientClass(RABBITMQ_URL)
        _rpc.publish_event(event_type, payload)
    except Exception as exc:
        log.debug("Event publish failed (non-critical): %s", exc)


def _actor(current: dict | None) -> dict:
    c = current or {}
    out = {"project_id": c.get("project_id")}
    uid = c.get("sub")
    if uid:
        out["user_id"] = uid
    return {k: v for k, v in out.items() if v}
