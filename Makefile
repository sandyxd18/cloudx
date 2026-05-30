.PHONY: up up-minio down logs ps reset install-cli test test-real-vm test-floating-ip \
        cleanup-network cleanup-compute cleanup-all \
        node-bootstrap lab-setup \
        bootstrap-provider-network cleanup-provider-network

# ── Bootstrap (run in order on fresh node) ────────────────────────────────────
# Step 1: Prepare the host OS (packages, OVS bridges, systemd service)
#         Must be run with sudo. Safe to re-run.
node-bootstrap:
	sudo ./scripts/node-bootstrap.sh

# Step 2a: Start all Docker services (image backend: file/lokal)
up:
	docker compose up -d --build

# Step 2b: Start semua Docker services + MinIO (IMAGE_STORAGE_BACKEND=minio)
#          Gunakan ini jika .env mengatur IMAGE_STORAGE_BACKEND=minio
up-minio:
	docker compose --profile minio up -d --build

# Step 3: Create lab networks, router, and download CirrOS image
#         Idempotent — safe to re-run.
lab-setup:
	./scripts/lab-setup.sh

# ── Shortcut: restart lab after reboot (no node-bootstrap needed) ─────────────
# Gunakan start atau start-minio sesuai IMAGE_STORAGE_BACKEND di .env
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

cleanup-all:
	./scripts/cleanup-all.sh

# ── CLI tools ─────────────────────────────────────────────────────────────────
install-cli:
	./scripts/install-cli.sh

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	pytest -q

test-real-vm:
	./scripts/test-real-vm.sh

test-floating-ip:
	./scripts/test-floating-ip.sh

# ── Provider network helpers (low-level, biasanya tidak perlu manual) ─────────
bootstrap-provider-network:
	sudo ./scripts/bootstrap-provider-network.sh --dry-run

cleanup-provider-network:
	sudo ./scripts/cleanup-provider-network.sh --dry-run
