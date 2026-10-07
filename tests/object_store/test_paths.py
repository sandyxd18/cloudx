import io

import pytest
from services.object_store.app.drivers.filesystem import FilesystemDriver, PathEscape


@pytest.fixture
def drv(tmp_path):
    return FilesystemDriver({"root": str(tmp_path / "root")}), tmp_path


def test_names_cannot_escape_the_root(drv):
    d, tmp = drv
    (tmp / "secret.txt").write_text("x")
    d.create_bucket("b1")
    for bucket, key in (("..", "secret.txt"), ("b1", "../secret.txt"), ("b1", "/etc/passwd"), ("b1/..", "k")):
        with pytest.raises(PathEscape):
            d.put_object(bucket, key, io.BytesIO(b"x"))
        with pytest.raises(PathEscape):
            d.get_object(bucket, key)
    with pytest.raises(PathEscape):
        d.delete_bucket("..")
    with pytest.raises(PathEscape):
        d.delete_object("b1", "..")  # the bucket itself is not an object
    assert (tmp / "secret.txt").exists()


def test_normal_nested_keys_work(drv):
    d, _ = drv
    d.create_bucket("b1")
    d.put_object("b1", "a/b/c.txt", io.BytesIO(b"hi"))
    assert d.get_object("b1", "a/b/c.txt") == b"hi"
