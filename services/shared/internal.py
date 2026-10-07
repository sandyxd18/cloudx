import os


def internal_headers() -> dict:
    """Header a calling service adds on /internal/* requests (empty when INTERNAL_TOKEN is unset)."""
    token = os.getenv("INTERNAL_TOKEN", "")
    return {"X-Internal-Token": token} if token else {}
