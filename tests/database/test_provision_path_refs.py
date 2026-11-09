import pytest

from tests.database.test_database_api import VM, db  # noqa: F401


def net_paths(c):
    return [x[2] for x in c.fake.calls if x[0] == "net" and x[2].startswith("/networks/")]


@pytest.mark.parametrize("bad", ["..", ".", ""])
def test_network_that_names_no_resource_fails_the_instance(db, bad):  # noqa: F811
    r = db.post("/instances", json={**VM, "network_id": bad})
    row = db.get(f"/instances/{r.json()['id']}").json()
    assert row["status"] == "error" and f"'{bad}' not found" in row["error"]
    assert net_paths(db) == []
    assert not [x for x in db.fake.calls if x[2] == "/servers"]


@pytest.mark.parametrize("ref,sent", [("a/b", "/networks/a%2Fb"), ("a?b", "/networks/a%3Fb")])
def test_network_is_one_path_segment(db, ref, sent):  # noqa: F811
    db.post("/instances", json={**VM, "network_id": ref})
    assert net_paths(db) == [sent]


def test_plain_network_is_sent_as_before(db):  # noqa: F811
    db.post("/instances", json=VM)
    assert net_paths(db) == ["/networks/n1"]
