import os
import requests

BLOCK_STORE = os.getenv("BLOCK_STORE_API_URL", "http://127.0.0.1:8004")
AUTH_API = os.getenv("AUTH_API_URL", "http://127.0.0.1:8005")

def test_volume_list_endpoint():
    resp = requests.post(f"{AUTH_API}/auth/tokens", json={"username": "admin", "password": "admin123"}, timeout=5)
    assert resp.status_code in (200, 201), resp.text
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    res = requests.get(f"{BLOCK_STORE}/volumes", headers=headers, timeout=5)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data

if __name__ == "__main__":
    test_volume_list_endpoint()
    print("Dashboard fix self-check passed!")
