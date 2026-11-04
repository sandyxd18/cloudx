import json
import os


def internal_headers() -> dict:
    """Header a calling service adds on /internal/* requests (empty when INTERNAL_TOKEN is unset)."""
    token = os.getenv("INTERNAL_TOKEN", "")
    return {"X-Internal-Token": token} if token else {}


CALLER_CLAIMS_HEADER = "X-Caller-Claims"
CALLER_CLAIMS_MAX_BYTES = 2048
CALLER_CLAIMS_KEYS = ("sub", "username", "project_id", "role", "roles", "domain_admin", "domain_id", "scope_type")


def on_behalf_headers(current: dict | None) -> dict:
    """`internal_headers()` plus X-Caller-Claims: the identity of `current`, stated by a service that holds INTERNAL_TOKEN.

    Receivers (image-api's optional_user_or_internal) take the claims as the caller's identity, so the call is judged
    as `current` and not as the synthetic admin. `current` None or the synthetic internal caller: admin, no claims.
    INTERNAL_TOKEN unset: {} (the call is anonymous). Raises ValueError when the claims exceed 2048 bytes: the
    receiver would read an oversized header as anonymous, so refuse here rather than send a call that cannot work.
    """
    headers = internal_headers()
    if not headers or current is None or current.get("sub") == "internal":
        return headers
    claims = {k: current[k] for k in CALLER_CLAIMS_KEYS if current.get(k) is not None}
    value = json.dumps(claims, separators=(",", ":"))  # ensure_ascii: the header stays ASCII
    if len(value.encode()) > CALLER_CLAIMS_MAX_BYTES:
        raise ValueError(f"{CALLER_CLAIMS_HEADER} would exceed {CALLER_CLAIMS_MAX_BYTES} bytes")
    return {**headers, CALLER_CLAIMS_HEADER: value}
