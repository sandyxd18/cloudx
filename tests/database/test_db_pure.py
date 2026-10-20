import pytest
from shared.db_cloudinit import PORTS, READY, render
from shared.db_validate import check_backend_storage, check_cidr, check_engine, check_password, gen_password

PW = "Abcdefghijk1_-x"
URL = "http://172.20.0.118:8019/internal/db-ready/abc"


@pytest.mark.parametrize("b,s", [("vm", "local"), ("vm", "block"), ("vm", "file"), ("container", "local")])
def test_matrix_ok(b, s):
    check_backend_storage(b, s)


@pytest.mark.parametrize("b,s", [("container", "block"), ("container", "file"), ("vm", "x"), ("lxc", "local")])
def test_matrix_rejected(b, s):
    with pytest.raises(ValueError):
        check_backend_storage(b, s)


def test_engine_password_cidr():
    assert check_engine("mariadb") == "mariadb"
    with pytest.raises(ValueError):
        check_engine("mysql")
    assert check_password(PW) == PW
    for bad in ("short", "a" * 65, "has space12345", "newline1234567\n", "quote'1234567890"):
        with pytest.raises(ValueError):
            check_password(bad)
    assert check_password(gen_password())
    assert check_cidr("10.0.0.5") == "10.0.0.5/32"
    for bad in ("0.0.0.0/0", "10.0.0.0/7", "::1/128", "x"):
        with pytest.raises(ValueError):
            check_cidr(bad)


def r(engine="postgresql", storage="local", **kw):
    return render(engine, storage, PW, URL, "tok_-1", cidr="10.0.0.0/24", **kw)


def test_render():
    pg = r()
    assert pg.startswith("#cloud-config") and PW in pg and URL in pg and "tok_-1" in pg
    assert "postgresql" in pg and "mariadb" not in pg and "lsblk" not in pg and "nfs4" not in pg
    assert "10.0.0.0/24" in pg
    blk = r("mariadb", "block")
    assert "mariadb-server" in blk and "lsblk" in blk and "mkfs.ext4" in blk and "nfs4" not in blk
    assert "alias /var/lib/mysql/" in blk
    nfs = r("postgresql", "file", nfs="10.0.0.9:/fs-1")
    assert "mount -t nfs4" in nfs and "10.0.0.9:/fs-1" in nfs and "nfs-common" in nfs and "runuser -u postgres" in nfs
    assert r() == r()
    assert PORTS == {"postgresql": 5432, "mariadb": 3306} and set(READY) == set(PORTS)


def test_render_rejects_unsafe():
    for kw in ({"engine": "x"}, {"storage": "file"}):
        with pytest.raises(ValueError):
            r(**kw)
    with pytest.raises(ValueError):
        render("postgresql", "local", "bad pw", URL, "t", cidr="10.0.0.0/24")
    with pytest.raises(ValueError):
        render("postgresql", "local", PW, "http://x;rm", "t", cidr="10.0.0.0/24")
    with pytest.raises(ValueError):
        render("postgresql", "file", PW, URL, "t", cidr="10.0.0.0/24", nfs="a b:/x")

@pytest.mark.parametrize("engine", ["postgresql", "mariadb"])
def test_render_restore_keeps_the_data(engine):
    ud = render(engine, "block", "pw1234567890", "http://1.2.3.4:8019/internal/db-ready/x", "tok", cidr="10.0.0.0/24", restore=True)
    assert ("ALTER USER 'root'@'%'" in ud) or engine == "postgresql"  # a restored root@% keeps its old password otherwise
    assert ('skip-grant-tables' in ud) == (engine == "mariadb")  # reset the restored root login before the normal start
    assert "ALTER USER" in ud and "mysql_install_db" not in ud and "pg_createcluster" not in ud and "pg_dropcluster" not in ud
    assert ("data_directory" in ud) if engine == "postgresql" else ("datadir" in ud)
    # an empty data disk still gets a filesystem only when blkid finds none (unchanged guard)
    assert 'blkid "$DEV" >/dev/null 2>&1 || mkfs.ext4' in ud
    fresh = render(engine, "block", "pw1234567890", "http://1.2.3.4:8019/internal/db-ready/x", "tok", cidr="10.0.0.0/24")
    assert ("mysql_install_db" in fresh) if engine == "mariadb" else ("pg_createcluster" in fresh)
