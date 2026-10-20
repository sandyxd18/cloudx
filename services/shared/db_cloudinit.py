"""cloud-config for a database VM. Every substituted value is validated first; nothing user-typed is interpolated raw."""
import re

from shared.db_validate import check_cidr, check_engine, check_password

PORTS = {"postgresql": 5432, "mariadb": 3306}
READY = {"postgresql": "ready to accept connections", "mariadb": "ready for connections"}
_URL = re.compile(r"\Ahttps?://[A-Za-z0-9.:-]+(/[A-Za-z0-9._/-]*)?\Z")
_TOKEN = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
_NFS = re.compile(r"\A[0-9.]+:/[A-Za-z0-9._/-]+\Z")

_DATA = """
DATA=/srv/dbdata
mkdir -p $DATA
{mount}
"""
_MOUNT = {
    "block": """DEV=
for i in $(seq 1 120); do
  DEV=$(lsblk -dnpo NAME,TYPE | while read d t; do [ "$t" = disk ] && [ -z "$(lsblk -no MOUNTPOINT "$d" | tr -d "[:space:]")" ] && echo "$d"; done | tail -1)
  [ -n "$DEV" ] && break; sleep 5
done
[ -n "$DEV" ] || {{ echo "no data disk"; exit 1; }}
blkid "$DEV" >/dev/null 2>&1 || mkfs.ext4 -q "$DEV"
echo "UUID=$(blkid -s UUID -o value "$DEV") $DATA ext4 defaults,nofail 0 2" >> /etc/fstab
mount $DATA""",
    "file": """for i in $(seq 1 60); do mount -t nfs4 {nfs} $DATA && break; sleep 10; done
mountpoint -q $DATA || {{ echo "nfs mount failed"; exit 1; }}
echo "{nfs} $DATA nfs4 defaults,_netdev 0 0" >> /etc/fstab""",
}

_PG = """systemctl stop postgresql
V=$(ls /usr/lib/postgresql | sort -V | tail -1)
{move}
PGC=/etc/postgresql/$V/main
sed -i "s/^#\\?listen_addresses.*/listen_addresses = '*'/" $PGC/postgresql.conf
echo "host all all {cidr} scram-sha-256" >> $PGC/pg_hba.conf
systemctl start postgresql
runuser -u postgres -- psql -c "ALTER USER postgres PASSWORD '{pw}'"
"""
_PG_MOVE = """pg_dropcluster --stop $V main
chown postgres:postgres $DATA || true
runuser -u postgres -- mkdir -p $DATA/pgdata
runuser -u postgres -- chmod 700 $DATA/pgdata
pg_createcluster $V main -d $DATA/pgdata"""

_MY = """systemctl stop mariadb
{move}
cat > /etc/mysql/mariadb.conf.d/99-cloudx.cnf <<CNF
[mysqld]
bind-address = 0.0.0.0
CNF
systemctl start mariadb
mariadb -e "ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('{pw}'); CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY '{pw}'; ALTER USER 'root'@'%' IDENTIFIED BY '{pw}'; GRANT ALL ON *.* TO 'root'@'%' WITH GRANT OPTION; FLUSH PRIVILEGES"
"""
_MY_MOVE = """echo "alias /var/lib/mysql/ -> $DATA/mysql/," >> /etc/apparmor.d/tunables/alias
systemctl reload apparmor || true
chown mysql:mysql $DATA || true
runuser -u mysql -- mkdir -p $DATA/mysql
runuser -u mysql -- mysql_install_db --datadir=$DATA/mysql --auth-root-authentication-method=normal >/dev/null
sed -i "s|^datadir.*|datadir = $DATA/mysql|" /etc/mysql/mariadb.conf.d/50-server.cnf
grep -q "^datadir" /etc/mysql/mariadb.conf.d/50-server.cnf || echo -e "[mysqld]\\ndatadir = $DATA/mysql" > /etc/mysql/mariadb.conf.d/60-cloudx-data.cnf"""

# Restore: the data directory already holds a database from the backup, so point the engine at it (no initdb/install_db).
_PG_RESTORE = """sed -i "s|^#\\?data_directory.*|data_directory = '$DATA/pgdata'|" /etc/postgresql/$V/main/postgresql.conf
chown -R postgres:postgres $DATA/pgdata
chmod 700 $DATA/pgdata"""
_MY_RESTORE = """echo "alias /var/lib/mysql/ -> $DATA/mysql/," >> /etc/apparmor.d/tunables/alias
systemctl reload apparmor || true
sed -i "s|^datadir.*|datadir = $DATA/mysql|" /etc/mysql/mariadb.conf.d/50-server.cnf
grep -q "^datadir" /etc/mysql/mariadb.conf.d/50-server.cnf || echo -e "[mysqld]\\ndatadir = $DATA/mysql" > /etc/mysql/mariadb.conf.d/60-cloudx-data.cnf
# the backup's root@localhost has the old password and no socket login: reset it with grants off, no network
systemctl set-environment MYSQLD_OPTS="--skip-grant-tables --skip-networking"
systemctl start mariadb
mariadb -e "FLUSH PRIVILEGES; ALTER USER 'root'@'localhost' IDENTIFIED VIA unix_socket OR mysql_native_password USING PASSWORD('{pw}')"
systemctl stop mariadb
systemctl unset-environment MYSQLD_OPTS"""

def render(engine: str, storage: str, password: str, callback_url: str, token: str, *, cidr: str,
           nfs: str | None = None, restore: bool = False) -> str:
    check_engine(engine)
    check_password(password)
    cidr = check_cidr(cidr)
    if storage not in ("local", "block", "file"):
        raise ValueError("storage must be local, block or file")
    if not _URL.match(callback_url) or not _TOKEN.match(token):
        raise ValueError("invalid callback url or token")
    if storage == "file" and not (nfs and _NFS.match(nfs)):
        raise ValueError("file storage needs a valid nfs target host:/path")
    pkg = "postgresql" if engine == "postgresql" else "mariadb-server"
    pkgs = [pkg] + (["nfs-common"] if storage == "file" else [])
    local = storage == "local"
    body = "" if local else _DATA.format(mount=_MOUNT[storage].format(nfs=nfs))
    if engine == "postgresql":
        move = "" if local else (_PG_RESTORE if restore else _PG_MOVE)
        body += _PG.format(move=move, cidr=cidr, pw=password)
    else:
        move = "" if local else (_MY_RESTORE.format(pw=password) if restore else _MY_MOVE)
        body += _MY.format(move=move, pw=password)
    body += f"""for i in $(seq 1 30); do curl -fsS -X POST -H 'X-Ready-Token: {token}' '{callback_url}' && break; sleep 10; done
"""
    script = "#!/bin/bash\nset -eu\nexec > >(tee -a /var/log/cloudx-db-setup.log >/dev/console) 2>&1\ntrap 'echo \"cloudx-db-setup FAILED at line $LINENO\"' ERR\n" + body
    ind = "\n".join("      " + ln if ln else "" for ln in script.splitlines())
    return (
        "#cloud-config\n"
        "package_update: true\n"
        "packages: [" + ", ".join(pkgs) + ", curl]\n"
        "write_files:\n"
        "  - path: /usr/local/sbin/cloudx-db-setup.sh\n"
        "    permissions: '0700'\n"
        "    content: |\n" + ind + "\n"
        "runcmd:\n"
        "  - [bash, /usr/local/sbin/cloudx-db-setup.sh]\n"
    )