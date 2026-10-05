"""Dashboard login / session / project scoping against a running lab (`pytest --live`)."""
import os
import uuid

import pytest
import requests

AUTH_API = os.getenv("AUTH_API_URL", "http://127.0.0.1:8005")
BLOCK_STORE = os.getenv("BLOCK_STORE_API_URL", "http://127.0.0.1:8004")
DASHBOARD_API = os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8080/api")
ADMIN_PASSWORD = os.environ["AUTH_ADMIN_PASSWORD"]


@pytest.fixture
def member(auth_headers):
    """A project with one non-admin user in it; both are removed afterwards."""
    suffix = uuid.uuid4().hex[:6]
    project, username, password = f"live-demo-{suffix}", f"live-user-{suffix}", f"Pw-{uuid.uuid4().hex}"
    r = requests.post(f"{AUTH_API}/projects", headers=auth_headers, json={"name": project}, timeout=10)
    assert r.status_code == 201, r.text
    r = requests.post(f"{AUTH_API}/users", headers=auth_headers, timeout=10, json={
        "username": username, "email": f"{username}@example.test", "password": password, "project_id": project})
    assert r.status_code == 201, r.text
    yield {"username": username, "password": password, "project": project}
    requests.delete(f"{AUTH_API}/users/{username}", headers=auth_headers, timeout=10)
    requests.delete(f"{AUTH_API}/projects/{project}", headers=auth_headers, timeout=10)


def test_volume_list_endpoint(auth_headers):
    res = requests.get(f"{BLOCK_STORE}/volumes", headers=auth_headers, timeout=5)
    assert res.status_code == 200
    assert "items" in res.json()


def test_dashboard_session_flow():
    s = requests.Session()
    r = s.post(f"{DASHBOARD_API}/auth/session/login", json={"username": "admin", "password": ADMIN_PASSWORD}, timeout=5)
    assert r.status_code == 200, r.text

    r_me = s.get(f"{DASHBOARD_API}/auth/session/me", timeout=5)
    assert r_me.status_code == 200, r_me.text
    assert r_me.json().get("username") == "admin"


def test_user_project_scoping_and_login(member):
    s_member = requests.Session()
    r_login = s_member.post(f"{DASHBOARD_API}/auth/session/login", timeout=5,
                            json={"username": member["username"], "password": member["password"]})
    assert r_login.status_code == 200, f"Login failed: {r_login.text}"
    login_data = r_login.json()
    assert login_data.get("user", {}).get("username") == member["username"]
    assert login_data.get("project", {}).get("name") == member["project"]

    r_me = s_member.get(f"{DASHBOARD_API}/auth/session/me", timeout=5)
    assert r_me.status_code == 200, f"Session me failed: {r_me.text}"
    assert r_me.json().get("project_name") == member["project"]

    # a non-admin only sees the projects they belong to
    r_projects = s_member.get(f"{DASHBOARD_API}/auth/projects", timeout=5)
    assert r_projects.status_code == 200, f"Get projects failed: {r_projects.text}"
    names = [p["name"] for p in r_projects.json().get("items", [])]
    assert names == [member["project"]], f"Non-admin user should only see their own project, got {names}"

    # ...and cannot switch to a project they are not assigned to
    r_switch = s_member.post(f"{DASHBOARD_API}/auth/session/switch-project", json={"project_id": "admin"}, timeout=5)
    assert r_switch.status_code == 403, f"Expected 403 for a non-admin switching to an unassigned project, got {r_switch.status_code}"

    # the admin sees all of them
    s_admin = requests.Session()
    r_admin_login = s_admin.post(f"{DASHBOARD_API}/auth/session/login", json={"username": "admin", "password": ADMIN_PASSWORD}, timeout=5)
    assert r_admin_login.status_code == 200
    r_admin_projects = s_admin.get(f"{DASHBOARD_API}/auth/projects", timeout=5)
    assert r_admin_projects.status_code == 200
    admin_names = [p["name"] for p in r_admin_projects.json().get("items", [])]
    assert "admin" in admin_names and member["project"] in admin_names, f"Admin should see all projects, got {admin_names}"
