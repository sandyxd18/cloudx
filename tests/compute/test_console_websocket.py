from fastapi.testclient import TestClient

from tests.stack import fresh_service


def test_compute_serial_console_websocket(monkeypatch, tmp_path):
    db_path = tmp_path / "compute_ws.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    compute_main = fresh_service("compute", monkeypatch, tmp_path)

    client = TestClient(compute_main.app)

    with client.websocket_connect("/servers/srv-123/console/ws") as websocket:
        msg = websocket.receive_text()
        assert "Connected to CloudX Serial Console" in msg
        websocket.send_text("ls -la")
        resp = websocket.receive_text()
        assert resp == "echo: ls -la"
