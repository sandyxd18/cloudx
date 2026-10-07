import os, time, requests, uuid

AUTH_URL = "http://127.0.0.1:8005"
COMPUTE_URL = "http://127.0.0.1:8003"
BLOCK_STORE_URL = "http://127.0.0.1:8004"

ADMIN_PASSWORD = ""
with open("/etc/cloudx/cloudx.env") as f:
    for line in f:
        if line.startswith("AUTH_ADMIN_PASSWORD="):
            ADMIN_PASSWORD = line.split("=", 1)[1].strip()

print("=== Task 2.4: Non-admin user of project A cannot GET project B volume ===")
adm_resp = requests.post(AUTH_URL + "/auth/tokens", json={"username": "admin", "password": ADMIN_PASSWORD}, timeout=5)
adm_headers = {"Authorization": "Bearer " + adm_resp.json()["token"]}

proj_a = "proj-a-" + uuid.uuid4().hex[:6]
proj_b = "proj-b-" + uuid.uuid4().hex[:6]
res_pa = requests.post(AUTH_URL + "/auth/projects", json={"name": proj_a}, headers=adm_headers, timeout=5).json()
res_pb = requests.post(AUTH_URL + "/auth/projects", json={"name": proj_b}, headers=adm_headers, timeout=5).json()

user_a = "user-a-" + uuid.uuid4().hex[:6]
user_b = "user-b-" + uuid.uuid4().hex[:6]
pass_a = "SecretPass123!"
pass_b = "SecretPass123!"

requests.post(AUTH_URL + "/auth/users", json={"username": user_a, "email": user_a + "@example.com", "password": pass_a, "project_id": res_pa["id"], "role": "member"}, headers=adm_headers, timeout=5)
requests.post(AUTH_URL + "/auth/users", json={"username": user_b, "email": user_b + "@example.com", "password": pass_b, "project_id": res_pb["id"], "role": "member"}, headers=adm_headers, timeout=5)

t_a_resp = requests.post(AUTH_URL + "/auth/tokens", json={"username": user_a, "password": pass_a}, timeout=5)
t_b_resp = requests.post(AUTH_URL + "/auth/tokens", json={"username": user_b, "password": pass_b}, timeout=5)

t_a = t_a_resp.json()["token"]
t_b = t_b_resp.json()["token"]

h_a = {"Authorization": "Bearer " + t_a}
h_b = {"Authorization": "Bearer " + t_b}

v_b_resp = requests.post(BLOCK_STORE_URL + "/volumes", json={"name": "vol-proj-b", "size": 1}, headers=h_b, timeout=5)
print("Created volume in Project B status:", v_b_resp.status_code, "vol:", v_b_resp.json().get("id"))
vol_b_id = v_b_resp.json()["id"]

get_cross = requests.get(BLOCK_STORE_URL + "/volumes/" + vol_b_id, headers=h_a, timeout=5)
print("User A GET volume of Project B status:", get_cross.status_code, "response:", get_cross.text)
