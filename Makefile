.PHONY: start stop status restart logs install-systemd migrate generate-certs reset package deb install-cli test test-live lint test-real-vm test-floating-ip test-metadata test-security-group test-volume-boot test-multi-network \
        cleanup-network cleanup-compute cleanup-storage cleanup-all \
        node-bootstrap dashboard-dev

# ── Bootstrap (run in order on fresh node) ────────────────────────────────────
# Step 1: Prepare the host OS (packages, OVS bridges, systemd service)
node-bootstrap:
	sudo ./scripts/node-bootstrap.sh

# Step 2: Install systemd units, configuration files, and Python virtualenv
install-systemd:
	sudo ./scripts/install-systemd.sh

# Step 3 (control plane): create/upgrade every service's database schema. Safe to re-run; also adopts existing installs.
migrate:
	sudo CLOUDX_SERVICES_DIR=/opt/cloudx/services /opt/cloudx/venv/bin/cloudx-db-migrate

# ── Service management (systemd native) ───────────────────────────────────────
start:
	sudo systemctl start 'cloudx-*'

stop:
	sudo systemctl stop 'cloudx-*'

restart:
	sudo systemctl restart 'cloudx-*'

status:
	sudo systemctl status 'cloudx-*' --no-pager

logs:
	sudo journalctl -u 'cloudx-*' -f

# Internal TLS PKI: generates CA + per-service certs under services/certs/out/.
generate-certs:
	./services/certs/generate-certs.sh

# ── Cleanup ───────────────────────────────────────────────────────────────────
reset:
	./scripts/reset.sh

cleanup-network:
	./scripts/cleanup-network.sh

cleanup-compute:
	./scripts/cleanup-compute.sh

cleanup-storage:
	./scripts/cleanup-storage.sh

cleanup-all:
	./scripts/cleanup-all.sh

# ── Release packaging ────────────────────────────────────────────────────────
package:
	./scripts/build-tarball.sh

# .deb packages from packaging/manifest.toml into dist/deb/ (needs dpkg-deb, fakeroot and network for the venv).
deb:
	bash ./scripts/build-deb.sh

# ── CLI install ───────────────────────────────────────────────────────────────
install-cli:
	./scripts/install-cli.sh

# ── Tests ─────────────────────────────────────────────────────────────────────
# Unit tests: no lab needed (this is what CI runs).
test:
	pytest -q

# Live API tests against a running lab; needs AUTH_ADMIN_PASSWORD (see /etc/cloudx/cloudx.env on the controller).
test-live:
	pytest -q --live tests/live

lint:
	ruff check services cli tests --select F401,F811,F821 --exclude "node_modules,migrations/versions"

test-real-vm:
	./scripts/test-real-vm.sh

test-floating-ip:
	./scripts/test-floating-ip.sh

test-metadata:
	./scripts/test-metadata.sh

test-security-group:
	./scripts/test-security-group.sh

test-volume-boot:
	./scripts/test-volume-boot.sh

test-multi-network:
	./scripts/test-multi-network.sh

dashboard-dev:
	cd services/dashboard/frontend && npm run dev
