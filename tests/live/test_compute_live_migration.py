from __future__ import annotations

import os

import requests

COMPUTE_URL = os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003")

class TestComputeLiveMigration:
    def test_migration_candidates_endpoint(self, auth_headers):
        # We just test the endpoint shape; real migration requires a running VM.
        r = requests.get(
            f"{COMPUTE_URL}/servers/nonexistent/migration-candidates",
            headers=auth_headers,
            timeout=10,
        )
        # Should be 404 for nonexistent server
        assert r.status_code == 404
