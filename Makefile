.PHONY: up up-minio down logs ps reset install-cli test test-real-vm test-floating-ip test-metadata test-security-group test-volume-boot test-multi-network \
        cleanup-network cleanup-compute cleanup-storage cleanup-all \
        node-bootstrap lab-setup dashboard-dev

# ── Bootstrap (run in order on fresh node) ────────────────────────────────────
# Step 1: Prepare the host OS (packages, OVS bridges, systemd service)
#         Must be run with sudo. Safe to re-run.
node-bootstrap:
	sudo ./scripts/node-bootstrap.sh

# Step 2a: Start all Docker services (image backend: file/local)
up:
	docker compose up -d --build

# Step 2b: Start all Docker services + MinIO (IMAGE_STORAGE_BACKEND=minio)
up-minio:
	docker compose --profile minio up -d --build

# Step 3: Create lab networks, router, download CirrOS, configure routes
#         Idempotent — safe to re-run.
lab-setup:
	./scripts/lab-setup.sh

# ── Shortcut: restart lab after reboot (no node-bootstrap needed) ─────────────
start: up lab-setup
start-minio: up-minio lab-setup

# ── Service management ────────────────────────────────────────────────────────
down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

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

# ── CLI install ───────────────────────────────────────────────────────────────
# Installs the unified 'cloudx' command to ~/.local/bin
install-cli:
	pip3 install --user --break-system-packages tabulate click requests 2>/dev/null || \
	pip3 install --user tabulate click requests
	pip3 install --user --break-system-packages ./cli 2>/dev/null || \
	pip3 install --user ./cli
	@echo ""
	@echo "✓ cloudx CLI installed. Make sure ~/.local/bin is in your PATH:"
	@echo "  export PATH=\"\$$HOME/.local/bin:\$$PATH\""
	@echo ""
	@echo "Quick start:"
	@echo "  cloudx auth configure        # Setup credentials"
	@echo "  cloudx auth login            # Login"
	@echo "  cloudx server list           # List servers"

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	pytest -q

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

# ── Provider network helpers ──────────────────────────────────────────────────
bootstrap-provider-network:
	sudo ./scripts/bootstrap-provider-network.sh --dry-run

cleanup-provider-network:
	sudo ./scripts/cleanup-provider-network.sh --dry-run

# ── Dashboard (frontend dev mode) ────────────────────────────────────────────
# Runs the Vite dev server locally (requires node installed on host).
# The proxy is set to http://localhost:8080 (dashboard container port).
# Make sure the full stack is running first: make up
dashboard-dev:
	cd services/dashboard/frontend && npm install && npm run dev
